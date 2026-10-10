"""Semantic-context changes: business-context edits and use-case review decisions.

Every change runs in the caller's session transaction, in this order:

1. take the semantic-content advisory lock (serializes edits, decisions and crawl swaps);
2. validate (references against the latest crawl, transitions, term namespace);
3. apply the change;
4. compute the semantic version and append it to the history if it changed;
5. write the audit event **sensitively** (same transaction: if the audit write fails the
   change rolls back and the request is refused with 503 ``audit_unavailable``).

The caller commits.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from cohortsplit.audit import actions
from cohortsplit.audit.service import Actor, AuditEventIn, AuditService
from cohortsplit.auth.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
)
from cohortsplit.cohort_spec.draft import DraftCohortSpec, referenced_columns
from cohortsplit.config import ConfigError
from cohortsplit.crawler.sampling import SamplingPolicy
from cohortsplit.crawler.settings import CrawlerSettings, load_crawler_settings
from cohortsplit.semantic import repository as repo
from cohortsplit.semantic.context import (
    MAX_DESCRIPTION_LENGTH,
    BusinessContextIn,
    Definition,
    Synonyms,
    definition_references,
    normalize_term,
)
from cohortsplit.semantic.inventory import ReferenceProblem, SchemaInventory, check_definition
from cohortsplit.semantic.inventory import check_spec as check_spec_references
from cohortsplit.semantic.models import BusinessContextEntry
from cohortsplit.semantic.snapshot import withheld_reasons
from cohortsplit.semantic.version import EntryContent, SemanticVersion

UseCaseStatus = Literal["pending_review", "confirmed", "rejected", "needs_rereview"]

CONFIRMABLE = frozenset({"pending_review", "needs_rereview", "rejected"})
REJECTABLE = frozenset({"pending_review", "needs_rereview", "confirmed"})


class BusinessContextUpdate(BaseModel):
    """PUT body: everything but the (immutable) key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    synonyms: Synonyms = ()
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    definition: Definition

    def with_key(self, key: str) -> BusinessContextIn:
        return BusinessContextIn(
            key=key,
            synonyms=self.synonyms,
            description=self.description,
            definition=self.definition,
        )


NlRequest = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class UseCaseEdit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nl_request: NlRequest
    spec: DraftCohortSpec


class RejectBody(BaseModel):
    """Optional reject body; unknown fields are ignored (only ``note`` is ever read)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None


@dataclass(frozen=True)
class ChangeContext:
    """Who changes what, when (from the request)."""

    user_id: int
    now: datetime
    request_id: str | None


def _problems(problems: tuple[ReferenceProblem, ...]) -> list[dict[str, str]]:
    return [{"reference": p.reference, "problem": p.problem} for p in problems]


def invalid_reference(problems: tuple[ReferenceProblem, ...]) -> UnprocessableError:
    return UnprocessableError(
        "invalid_reference",
        "The definition references tables or columns that are not in the latest crawl.",
        problems=_problems(problems),
    )


def inventory_unavailable() -> ConflictError:
    return ConflictError(
        "schema_inventory_unavailable",
        "No crawl has been stored yet, so references cannot be checked. Run the crawler first.",
    )


def _entry_dict(content: EntryContent | None) -> dict[str, Any] | None:
    if content is None:
        return None
    return {
        "key": content.key,
        "kind": content.kind,
        "synonyms": list(content.synonyms),
        "description": content.description,
        "definition": dict(content.definition),
    }


def _content_of(data: BusinessContextIn) -> EntryContent:
    return EntryContent(
        key=data.key,
        kind=data.definition.kind,
        synonyms=data.synonyms,
        description=data.description,
        definition=data.definition.model_dump(mode="json", exclude_none=True),
    )


def _terms(content: EntryContent) -> frozenset[str]:
    return frozenset({normalize_term(content.key.replace("_", " ")), *content.synonyms})


class SemanticContextService:
    def __init__(
        self,
        audit: AuditService,
        crawler_settings: Callable[[], CrawlerSettings] = load_crawler_settings,
    ) -> None:
        self._audit = audit
        self._crawler_settings = crawler_settings

    def sampling_policy(self, db: Session) -> SamplingPolicy:
        """Today's sampling policy (settings + export grants read in ``db``). Fails closed:
        without a valid crawler configuration nothing is shown or versioned (503)."""
        try:
            settings = self._crawler_settings()
        except ConfigError as exc:
            raise ServiceUnavailableError("crawler_misconfigured", str(exc)) from None
        return repo.current_policy(db, settings)

    def current_version(self, db: Session) -> SemanticVersion:
        return repo.compute_current_version(db, self.sampling_policy(db))

    def withheld(self, db: Session, rows: list[Any]) -> dict[int, str]:
        """Generated use cases whose literals today's policy forbids, with the reason."""
        return withheld_reasons(
            [repo.use_case_row(r) for r in rows], repo.generated_docs(db), self.sampling_policy(db)
        )

    # -- shared steps ------------------------------------------------------------------

    def _finish(
        self,
        db: Session,
        ctx: ChangeContext,
        *,
        action: str,
        target_type: str,
        target_id: str,
        before: SemanticVersion,
        metadata: Mapping[str, object],
    ) -> SemanticVersion:
        db.flush()
        # Re-read what was stored (JSONB round trip), so the recorded version equals what
        # any later read computes (e.g. 1e20 is stored and read back as an integer).
        db.expire_all()
        after = self.current_version(db)
        repo.record_version_if_changed(
            db,
            after,
            cause=action,
            target=f"{target_type}:{target_id}",
            actor_user_id=ctx.user_id,
            now=ctx.now,
        )
        self._audit.record(
            db,
            AuditEventIn(
                action=action,
                outcome="success",
                actor=Actor.user(ctx.user_id),
                occurred_at=ctx.now,
                target_type=target_type,
                target_id=target_id,
                request_id=ctx.request_id,
                metadata={
                    **metadata,
                    "semantic_version_before": before.version,
                    "semantic_version_after": after.version,
                },
            ),
            sensitive=True,
        )
        return after

    @staticmethod
    def _inventory(db: Session) -> SchemaInventory | None:
        return repo.schema_inventory(repo.generated_docs(db))

    def _validate_entry(
        self, db: Session, data: BusinessContextIn, *, replacing: str | None
    ) -> None:
        tables, columns = definition_references(data.definition)
        if tables or columns:
            inventory = self._inventory(db)
            if inventory is None:
                raise inventory_unavailable()
            problems = check_definition(data.definition, inventory)
            if problems:
                raise invalid_reference(problems)
        others = [repo.entry_content(e) for e in repo.list_entries(db) if e.key != replacing]
        if data.definition.kind == "canonical_user_id" and any(
            o.kind == "canonical_user_id" for o in others
        ):
            raise ConflictError(
                "canonical_user_id_exists",
                "A canonical user identifier is already defined; edit that entry instead.",
            )
        mine = _terms(_content_of(data))
        for other in others:
            clash = sorted(mine & _terms(other))
            if clash:
                raise ConflictError(
                    "term_conflict",
                    f'The term "{clash[0]}" already belongs to entry "{other.key}".',
                    term=clash[0],
                    entry=other.key,
                )

    # -- business context --------------------------------------------------------------

    def create_entry(
        self, db: Session, data: BusinessContextIn, ctx: ChangeContext
    ) -> BusinessContextEntry:
        repo.lock_semantic_content(db)
        if repo.get_entry(db, data.key) is not None:
            raise ConflictError(
                "business_context_key_taken", f'An entry with key "{data.key}" already exists.'
            )
        self._validate_entry(db, data, replacing=None)
        before = self.current_version(db)
        content = _content_of(data)
        entry = BusinessContextEntry(
            key=content.key,
            kind=content.kind,
            synonyms=list(content.synonyms),
            description=content.description,
            definition=dict(content.definition),
            created_at=ctx.now,
            updated_at=ctx.now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        db.add(entry)
        self._finish(
            db,
            ctx,
            action=actions.SEMANTIC_CONTEXT_CREATE,
            target_type="business_context",
            target_id=data.key,
            before=before,
            metadata={"kind": content.kind, "before": None, "after": _entry_dict(content)},
        )
        return entry

    def update_entry(
        self, db: Session, key: str, update: BusinessContextUpdate, ctx: ChangeContext
    ) -> BusinessContextEntry:
        repo.lock_semantic_content(db)
        entry = repo.get_entry(db, key)
        if entry is None:
            raise NotFoundError("Business-context entry")
        data = update.with_key(key)
        self._validate_entry(db, data, replacing=key)
        before = self.current_version(db)
        old = repo.entry_content(entry)
        new = _content_of(data)
        entry.kind = new.kind
        entry.synonyms = list(new.synonyms)
        entry.description = new.description
        entry.definition = dict(new.definition)
        entry.updated_at = ctx.now
        entry.updated_by = ctx.user_id
        self._finish(
            db,
            ctx,
            action=actions.SEMANTIC_CONTEXT_UPDATE,
            target_type="business_context",
            target_id=key,
            before=before,
            metadata={"kind": new.kind, "before": _entry_dict(old), "after": _entry_dict(new)},
        )
        return entry

    def delete_entry(self, db: Session, key: str, ctx: ChangeContext) -> None:
        repo.lock_semantic_content(db)
        entry = repo.get_entry(db, key)
        if entry is None:
            raise NotFoundError("Business-context entry")
        before = self.current_version(db)
        old = repo.entry_content(entry)
        db.delete(entry)
        self._finish(
            db,
            ctx,
            action=actions.SEMANTIC_CONTEXT_DELETE,
            target_type="business_context",
            target_id=key,
            before=before,
            metadata={"kind": old.kind, "before": _entry_dict(old), "after": None},
        )

    # -- use-case review ---------------------------------------------------------------

    def _locked_use_case(self, db: Session, use_case_id: int) -> Any:
        repo.lock_semantic_content(db)
        row = repo.get_use_case(db, use_case_id, for_update=True)
        if row is None:
            raise NotFoundError("Use case")
        return row

    def _check_spec(self, db: Session, spec: DraftCohortSpec) -> None:
        inventory = self._inventory(db)
        if inventory is None:
            raise inventory_unavailable()
        problems = check_spec_references(spec, inventory)
        if problems:
            raise invalid_reference(problems)

    @staticmethod
    def _transition_error(row: Any, action: str) -> ConflictError:
        return ConflictError(
            "invalid_transition",
            f"A use case with status {row.status} cannot be {action}.",
            status=row.status,
        )

    def confirm(self, db: Session, use_case_id: int, ctx: ChangeContext) -> None:
        row = self._locked_use_case(db, use_case_id)
        if row.status not in CONFIRMABLE:
            raise self._transition_error(row, "confirmed")
        try:
            spec = DraftCohortSpec.model_validate(dict(row.spec))
        except ValueError:
            raise UnprocessableError(
                "invalid_spec", "The stored spec is no longer valid; edit the use case instead."
            ) from None
        self._check_spec(db, spec)
        reason = self.withheld(db, [row]).get(row.id)
        if reason is not None:
            raise ConflictError("use_case_withheld", f"This use case cannot be confirmed: {reason}")
        before = self.current_version(db)
        repo.update_use_case(
            db,
            use_case_id,
            {
                "status": "confirmed",
                "review_note": None,
                "reviewed_by": ctx.user_id,
                "reviewed_at": ctx.now,
                "updated_at": ctx.now,
            },
        )
        self._finish(
            db,
            ctx,
            action=actions.USE_CASE_CONFIRM,
            target_type="example_use_case",
            target_id=str(use_case_id),
            before=before,
            metadata={"from_status": row.status, "to_status": "confirmed", "origin": row.origin},
        )

    def reject(self, db: Session, use_case_id: int, note: str | None, ctx: ChangeContext) -> None:
        row = self._locked_use_case(db, use_case_id)
        if row.status not in REJECTABLE:
            raise self._transition_error(row, "rejected")
        before = self.current_version(db)
        repo.update_use_case(
            db,
            use_case_id,
            {
                "status": "rejected",
                "review_note": note or None,
                "reviewed_by": ctx.user_id,
                "reviewed_at": ctx.now,
                "updated_at": ctx.now,
            },
        )
        self._finish(
            db,
            ctx,
            action=actions.USE_CASE_REJECT,
            target_type="example_use_case",
            target_id=str(use_case_id),
            before=before,
            metadata={
                "from_status": row.status,
                "to_status": "rejected",
                "origin": row.origin,
                "note": note or None,
            },
        )

    def edit(self, db: Session, use_case_id: int, edit: UseCaseEdit, ctx: ChangeContext) -> None:
        """A reviewer rewrites the use case: it becomes human-authored and confirmed."""
        row = self._locked_use_case(db, use_case_id)
        self._check_spec(db, edit.spec)
        before = self.current_version(db)
        spec = edit.spec.model_dump(mode="json", exclude_none=True)
        repo.update_use_case(
            db,
            use_case_id,
            {
                "origin": "human",
                "status": "confirmed",
                "nl_request": edit.nl_request,
                "spec": spec,
                "spec_version": edit.spec.spec_version,
                "referenced_columns": sorted(referenced_columns(edit.spec)),
                "review_note": None,
                "reviewed_by": ctx.user_id,
                "reviewed_at": ctx.now,
                "updated_at": ctx.now,
            },
        )
        self._finish(
            db,
            ctx,
            action=actions.USE_CASE_EDIT,
            target_type="example_use_case",
            target_id=str(use_case_id),
            before=before,
            metadata={
                "from_status": row.status,
                "to_status": "confirmed",
                "origin_before": row.origin,
                "before": {"nl_request": row.nl_request, "spec": dict(row.spec)},
                "after": {"nl_request": edit.nl_request, "spec": spec},
            },
        )
