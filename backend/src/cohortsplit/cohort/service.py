"""Interpretation and preview orchestration (FR-4 .. FR-7, campaign steps 1-5).

Interpret (nothing is executed against the warehouse):

1. one semantic snapshot; refuse without a crawl or a canonical user id (S2);
2. deterministic pre-guards (mutation / analytics / sequence), no LLM call;
3. the LLM's tagged result from a snapshot-only prompt (bounded retries);
4. deterministic resolution: undefined terms / unknown values -> clarification, invented
   references -> rejection; otherwise compile + validate (a dry run) and store the
   interpretation for its creator.

Preview (after explicit confirmation of the spec hash and semantic version):

1. the interpretation must be the caller's, unexpired, and the semantic version unchanged
   (else 409 — re-interpret);
2. requested export columns are checked with the export-column policy;
3. deterministic SQL, AST-validated, executed only through the read-only executor
   (timeout, row cap); value lookups first, then membership, sample and NULL counts;
4. a prepared cohort (definition only) is stored and the run audited sensitively in the
   same transaction (fail closed).

Operational logs carry ids and hashes, never the request text or result values.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from cohortsplit.audit import actions
from cohortsplit.audit.service import Actor, AuditEventIn, AuditService, AuditWriteError, Outcome
from cohortsplit.auth.errors import ServiceUnavailableError
from cohortsplit.auth.policy import ExportColumnDeniedError, PolicyService, Principal
from cohortsplit.cohort import errors
from cohortsplit.cohort.environment import CohortEnvironment, QueryExecutor
from cohortsplit.cohort.guards import pre_guard
from cohortsplit.cohort.models import CohortInterpretation, PreparedCohortRow
from cohortsplit.cohort.store import get_interpretation, new_id, purge_expired
from cohortsplit.cohort_spec.common import table_of
from cohortsplit.cohort_spec.upgrade import load_spec
from cohortsplit.cohort_spec.v1 import CohortSpecV1, spec_document, spec_hash
from cohortsplit.compiler.readable import readable
from cohortsplit.compiler.resolve import (
    CanonicalUserIdMissingError,
    Invalid,
    ResolvedCohort,
    Unresolved,
    resolve,
)
from cohortsplit.compiler.sql import (
    AS_OF_PARAM,
    DIALECT,
    ID_COLUMN,
    CompiledQuery,
    compile_cohort,
    compile_null_counts,
    compile_sample,
    compile_value_lookup,
    sql_hash,
)
from cohortsplit.compiler.validator import SqlValidationError, validate_select
from cohortsplit.config import ConfigError, Settings
from cohortsplit.llm.client import LlmNotConfiguredError, LlmUnavailableError
from cohortsplit.llm.interpreter import (
    Clarification,
    Interpretation,
    LlmInvalidOutputError,
    Rejection,
    interpret_request,
)
from cohortsplit.llm.prompt import build_prompt
from cohortsplit.semantic.snapshot import SemanticContextSnapshot
from cohortsplit.warehouse.errors import (
    QueryTimeoutError,
    RowCapExceededError,
    WarehouseError,
    WarehouseNotConfiguredError,
    WarehouseUnavailableError,
)

logger = logging.getLogger(__name__)

EMPTY_REASON = "The cohort is empty: there is nothing to split or export."


@dataclass(frozen=True)
class RequestContext:
    principal: Principal
    now: datetime
    request_id: str | None


def _json_value(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _allowed_tables(snapshot: SemanticContextSnapshot) -> frozenset[str]:
    return frozenset(t.qualified_name for t in snapshot.tables)


def _definition_params(query: CompiledQuery) -> dict[str, Any]:
    return {k: v for k, v in query.params.items() if k != AS_OF_PARAM}


class CohortService:
    def __init__(self, audit: AuditService, policy: PolicyService, settings: Settings) -> None:
        self._audit = audit
        self._policy = policy
        self._interpretation_ttl = timedelta(minutes=settings.cohort_interpretation_ttl_minutes)
        self._prepared_ttl = timedelta(minutes=settings.cohort_prepared_ttl_minutes)
        self._sample_size = settings.cohort_preview_sample_size

    # -- shared --------------------------------------------------------------------------

    @staticmethod
    def _snapshot(env: CohortEnvironment) -> SemanticContextSnapshot:
        try:
            snapshot = env.snapshot()
        except ConfigError as exc:
            raise ServiceUnavailableError("crawler_misconfigured", str(exc)) from None
        if not snapshot.tables:
            raise errors.schema_inventory_unavailable()
        if snapshot.canonical_user_id is None:
            raise errors.canonical_user_id_missing(str(CanonicalUserIdMissingError()))
        return snapshot

    @staticmethod
    def _validate(query: CompiledQuery, snapshot: SemanticContextSnapshot) -> None:
        try:
            validate_select(query.sql, allowed_tables=_allowed_tables(snapshot))
        except SqlValidationError as exc:
            # The compiler must never produce invalid SQL: a defect, never executed.
            logger.error("cohort.sql_validation_failed defect reason=%s", exc.reason)
            raise errors.SqlValidationApiError() from None

    def _event(
        self,
        ctx: RequestContext,
        action: str,
        outcome: Outcome,
        *,
        target_type: str,
        target_id: str | None,
        metadata: dict[str, object],
    ) -> AuditEventIn:
        return AuditEventIn(
            action=action,
            outcome=outcome,
            actor=Actor.user(ctx.principal.user_id),
            occurred_at=ctx.now,
            target_type=target_type,
            target_id=target_id,
            request_id=ctx.request_id,
            metadata=metadata,
        )

    # -- interpret -----------------------------------------------------------------------

    def interpret(
        self, db: Session, env: CohortEnvironment, nl_request: str, ctx: RequestContext
    ) -> dict[str, Any]:
        snapshot = self._snapshot(env)
        version = snapshot.semantic_version
        base = {"nl_request": nl_request, "semantic_version": version}

        def error(code: str) -> None:
            self._audit.record_detached(
                self._event(
                    ctx,
                    actions.COHORT_INTERPRET,
                    "error",
                    target_type="cohort_interpretation",
                    target_id=None,
                    metadata={**base, "error": code},
                ),
                sensitive=False,
            )
            logger.info(
                "cohort.interpret request_id=%s outcome=error error=%s semantic_version=%s",
                ctx.request_id,
                code,
                version,
            )

        guarded = pre_guard(nl_request)
        if guarded is not None:
            return self._answer(db, ctx, base, guarded)

        db.rollback()  # release the appdb connection while the LLM thinks
        try:
            connection = env.llm()
        except LlmNotConfiguredError as exc:
            error("llm_not_configured")
            raise errors.llm_not_configured(str(exc)) from None
        except ConfigError as exc:
            error("llm_misconfigured")
            raise errors.llm_misconfigured(str(exc)) from None
        try:
            result = interpret_request(
                connection.client,
                build_prompt(snapshot, nl_request),
                max_attempts=connection.max_attempts,
            )
        except LlmUnavailableError as exc:
            error("llm_unavailable")
            raise errors.llm_unavailable(str(exc)) from None
        except LlmInvalidOutputError as exc:
            error("llm_invalid_output")
            raise errors.LlmInvalidOutputApiError(str(exc)) from None

        if not isinstance(result, Interpretation):
            return self._answer(db, ctx, base, result)
        try:
            resolution = resolve(result.spec, snapshot)
        except CanonicalUserIdMissingError as exc:
            raise errors.canonical_user_id_missing(str(exc)) from None
        if isinstance(resolution, Unresolved):
            clarification = {
                "kind": "clarification",
                "question": resolution.question,
                "unknown_terms": list(resolution.unknown_terms),
                "unknown_values": [
                    {"column": v.column, "value": v.value, "suggestions": list(v.suggestions)}
                    for v in resolution.unknown_values
                ],
            }
            return self._record_interpret(db, ctx, base, clarification, "success")
        if isinstance(resolution, Invalid):
            rejection = Rejection(
                reason="unknown_term",
                message=(
                    "The interpretation refers to data that is not in the semantic context: "
                    + "; ".join(resolution.problems)
                    + ". Rephrase the request or ask an analyst to document it."
                )[:2000],
            )
            return self._answer(db, ctx, base, rejection)
        return self._store_interpretation(db, ctx, base, result.spec, resolution, snapshot)

    def _answer(
        self,
        db: Session,
        ctx: RequestContext,
        base: dict[str, Any],
        result: Clarification | Rejection,
    ) -> dict[str, Any]:
        if isinstance(result, Clarification):
            body: dict[str, Any] = {
                "kind": "clarification",
                "question": result.question,
                "unknown_terms": [],
                "unknown_values": [],
            }
            return self._record_interpret(db, ctx, base, body, "success")
        body = {"kind": "rejection", "reason": result.reason, "message": result.message}
        return self._record_interpret(db, ctx, base, body, "denied")

    def _record_interpret(
        self,
        db: Session,
        ctx: RequestContext,
        base: dict[str, Any],
        body: dict[str, Any],
        outcome: Outcome,
        *,
        target_id: str | None = None,
        spec_digest: str | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, object] = {**base, "kind": body["kind"]}
        if body["kind"] == "rejection":
            metadata["reason"] = body["reason"]
        if body["kind"] == "clarification":
            metadata["unknown_terms"] = body["unknown_terms"]
        if spec_digest is not None:
            metadata["spec_hash"] = spec_digest
        self._audit.record(
            db,
            self._event(
                ctx,
                actions.COHORT_INTERPRET,
                outcome,
                target_type="cohort_interpretation",
                target_id=target_id,
                metadata=metadata,
            ),
            sensitive=False,
        )
        db.commit()
        logger.info(
            "cohort.interpret request_id=%s kind=%s reason=%s spec_hash=%s semantic_version=%s",
            ctx.request_id,
            body["kind"],
            body.get("reason"),
            spec_digest,
            base["semantic_version"],
        )
        return {**body, "semantic_version": base["semantic_version"]}

    def _store_interpretation(
        self,
        db: Session,
        ctx: RequestContext,
        base: dict[str, Any],
        spec: CohortSpecV1,
        resolved: ResolvedCohort,
        snapshot: SemanticContextSnapshot,
    ) -> dict[str, Any]:
        # Dry run: the definition must compile to valid SQL before it is offered.
        self._validate(compile_cohort(resolved, as_of=ctx.now), snapshot)
        digest = spec_hash(spec)
        text = readable(resolved)
        purge_expired(db, ctx.now)
        row = CohortInterpretation(
            id=new_id(),
            created_by=ctx.principal.user_id,
            created_at=ctx.now,
            expires_at=ctx.now + self._interpretation_ttl,
            nl_request=base["nl_request"],
            spec=spec_document(spec),
            spec_hash=digest,
            semantic_version=snapshot.semantic_version,
            readable=text,
        )
        db.add(row)
        db.flush()
        body = {
            "kind": "interpretation",
            "interpretation_id": row.id,
            "readable": text,
            "spec": spec_document(spec),
            "spec_hash": digest,
            "expires_at": row.expires_at.isoformat(),
        }
        return self._record_interpret(
            db, ctx, base, body, "success", target_id=row.id, spec_digest=digest
        )

    # -- preview -------------------------------------------------------------------------

    def preview(
        self,
        db: Session,
        env: CohortEnvironment,
        *,
        interpretation_id: str,
        confirmed_spec_hash: str,
        confirmed_version: str,
        export_columns: list[str],
        ctx: RequestContext,
    ) -> dict[str, Any]:
        interpretation = get_interpretation(
            db, interpretation_id, user_id=ctx.principal.user_id, now=ctx.now
        )
        if interpretation is None:
            raise errors.InterpretationNotFoundError()
        if (confirmed_spec_hash, confirmed_version) != (
            interpretation.spec_hash,
            interpretation.semantic_version,
        ):
            raise errors.confirmation_mismatch()
        snapshot = self._snapshot(env)
        if snapshot.semantic_version != interpretation.semantic_version:
            raise errors.semantic_version_changed(
                interpretation.semantic_version, snapshot.semantic_version
            )
        try:
            resolution = resolve(load_spec(interpretation.spec), snapshot)
        except CanonicalUserIdMissingError as exc:
            raise errors.canonical_user_id_missing(str(exc)) from None
        if not isinstance(resolution, ResolvedCohort):  # same version: cannot happen
            raise errors.semantic_version_changed(
                interpretation.semantic_version, snapshot.semantic_version
            )
        resolved = resolution
        run = _Run(
            nl_request=interpretation.nl_request,
            spec=dict(interpretation.spec),
            spec_hash=interpretation.spec_hash,
            semantic_version=interpretation.semantic_version,
            interpretation_id=interpretation.id,
            readable=interpretation.readable,
        )
        columns = self._export_columns(db, ctx, run, resolved, export_columns)
        compiled = compile_cohort(resolved, as_of=ctx.now)
        self._validate(compiled, snapshot)
        run.sql_hash = sql_hash(compiled)
        db.rollback()  # release the appdb connection during warehouse work
        try:
            executor = env.executor()
        except WarehouseNotConfiguredError as exc:
            self._run_failed(ctx, run, "warehouse_not_configured")
            raise ServiceUnavailableError("warehouse_not_configured", str(exc)) from None
        try:
            self._check_lookups(executor, resolved, snapshot, ctx, run)
            membership = executor.execute(compiled.sql, compiled.params)
            total = len(membership.rows)
            sample = self._sample(executor, resolved, snapshot, columns, total, ctx.now)
            null_counts = self._null_counts(executor, resolved, snapshot, columns, total, ctx.now)
        except RowCapExceededError as exc:
            self._run_failed(ctx, run, "row_cap_exceeded")
            raise errors.RowCapError(exc.row_cap) from None
        except QueryTimeoutError as exc:
            self._run_failed(ctx, run, "query_timeout")
            raise errors.QueryTimeoutApiError(str(exc)) from None
        except WarehouseUnavailableError as exc:
            self._run_failed(ctx, run, "warehouse_unavailable")
            raise errors.WarehouseApiError(503, "warehouse_unavailable", str(exc)) from None
        except WarehouseError as exc:
            self._run_failed(ctx, run, "warehouse_error")
            raise errors.WarehouseApiError(502, "warehouse_error", str(exc)) from None

        prepared_id: str | None = None
        expires_at: datetime | None = None
        if total > 0:
            prepared_id = new_id()
            expires_at = ctx.now + self._prepared_ttl
            db.add(
                PreparedCohortRow(
                    id=prepared_id,
                    interpretation_id=interpretation_id,
                    created_by=ctx.principal.user_id,
                    created_at=ctx.now,
                    expires_at=expires_at,
                    nl_request=run.nl_request,
                    spec=run.spec,
                    spec_hash=run.spec_hash,
                    sql=compiled.sql,
                    params=dict(compiled.params),
                    sql_hash=run.sql_hash,
                    semantic_version=run.semantic_version,
                    dialect=DIALECT,
                    as_of=ctx.now,
                    row_count=total,
                    export_columns=columns,
                )
            )
            db.flush()
        # Sensitive: written in the same transaction as the prepared cohort (fail closed).
        self._audit.record(
            db,
            self._event(
                ctx,
                actions.COHORT_RUN,
                "success",
                target_type="prepared_cohort",
                target_id=prepared_id,
                metadata={**run.metadata(), "row_count": total, "export_columns": columns},
            ),
            sensitive=True,
        )
        db.commit()
        logger.info(
            "cohort.run request_id=%s outcome=success spec_hash=%s sql_hash=%s "
            "semantic_version=%s row_count=%d",
            ctx.request_id,
            run.spec_hash,
            run.sql_hash,
            run.semantic_version,
            total,
        )
        return {
            "prepared_cohort_id": prepared_id,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "total_count": total,
            "empty": total == 0,
            "split_export_enabled": total > 0,
            "disabled_reason": None if total else EMPTY_REASON,
            "sample": sample,
            "null_counts": null_counts,
            "readable": run.readable,
            "sql": compiled.sql,
            "sql_params": _definition_params(compiled),
            "as_of": ctx.now.isoformat(),
            "sql_hash": run.sql_hash,
            "spec_hash": run.spec_hash,
            "semantic_version": run.semantic_version,
            "row_cap": executor.row_cap,
        }

    def _export_columns(
        self,
        db: Session,
        ctx: RequestContext,
        run: "_Run",
        resolved: ResolvedCohort,
        requested: list[str],
    ) -> list[str]:
        canonical = f"{resolved.user_table}.{resolved.user_key}"
        columns = [c for c in dict.fromkeys(requested) if c != canonical]
        allowed = self._policy.effective_export_columns(db, ctx.principal)
        denied = [c for c in columns if not allowed.allows(c)]
        if denied:
            self._run_failed(ctx, run, "export_column_denied", outcome="denied", denied=denied)
            raise ExportColumnDeniedError(denied)
        unsupported = [
            c
            for c in columns
            if table_of(c) != resolved.user_table
            or c.rsplit(".", 1)[1] not in resolved.user_columns
        ]
        if unsupported:
            raise errors.ExportColumnUnsupportedError(unsupported, resolved.user_table)
        return columns

    def _check_lookups(
        self,
        executor: QueryExecutor,
        resolved: ResolvedCohort,
        snapshot: SemanticContextSnapshot,
        ctx: RequestContext,
        run: "_Run",
    ) -> None:
        missing: list[tuple[str, str]] = []
        for lookup in resolved.lookups:
            query = compile_value_lookup(lookup)
            self._validate(query, snapshot)
            found = executor.execute(query.sql, query.params, row_cap=len(lookup.values))
            present = {str(row[0]) for row in found.rows}
            missing += [(lookup.column, str(v)) for v in lookup.values if str(v) not in present]
        if missing:
            self._run_failed(ctx, run, "unknown_value", outcome="denied")
            raise errors.UnknownValueError(missing)

    def _sample(
        self,
        executor: QueryExecutor,
        resolved: ResolvedCohort,
        snapshot: SemanticContextSnapshot,
        columns: list[str],
        total: int,
        now: datetime,
    ) -> dict[str, Any]:
        names = [ID_COLUMN, *columns]
        if total == 0:
            return {"columns": names, "rows": [], "limit": self._sample_size}
        query = compile_sample(resolved, columns=columns, limit=self._sample_size, as_of=now)
        self._validate(query, snapshot)
        result = executor.execute(query.sql, query.params, row_cap=self._sample_size)
        rows = [[_json_value(v) for v in row] for row in result.rows]
        return {"columns": names, "rows": rows, "limit": self._sample_size}

    def _null_counts(
        self,
        executor: QueryExecutor,
        resolved: ResolvedCohort,
        snapshot: SemanticContextSnapshot,
        columns: list[str],
        total: int,
        now: datetime,
    ) -> dict[str, int]:
        if not columns or total == 0:
            return dict.fromkeys(columns, 0)
        query = compile_null_counts(resolved, columns=columns, as_of=now)
        self._validate(query, snapshot)
        [row] = executor.execute(query.sql, query.params, row_cap=1).rows
        return {column: int(str(row[i + 1])) for i, column in enumerate(columns)}

    def _run_failed(
        self,
        ctx: RequestContext,
        run: "_Run",
        code: str,
        *,
        outcome: Outcome = "error",
        denied: list[str] | None = None,
    ) -> None:
        metadata: dict[str, object] = {**run.metadata(), "error": code}
        if denied is not None:
            metadata["denied_columns"] = denied
        try:
            self._audit.record_detached(
                self._event(
                    ctx,
                    actions.COHORT_RUN,
                    outcome,
                    target_type="cohort_interpretation",
                    target_id=run.interpretation_id,
                    metadata=metadata,
                ),
                sensitive=outcome == "denied",
            )
        except AuditWriteError:
            logger.error("cohort.run %s could not be audited; refusing anyway", code)
        logger.info(
            "cohort.run request_id=%s outcome=%s error=%s spec_hash=%s semantic_version=%s",
            ctx.request_id,
            outcome,
            code,
            run.spec_hash,
            run.semantic_version,
        )


@dataclass
class _Run:
    nl_request: str
    spec: dict[str, Any]
    spec_hash: str
    semantic_version: str
    interpretation_id: str
    readable: str
    sql_hash: str | None = None

    def metadata(self) -> dict[str, object]:
        return {
            "nl_request": self.nl_request,
            "spec_hash": self.spec_hash,
            "sql_hash": self.sql_hash,
            "semantic_version": self.semantic_version,
            "interpretation_id": self.interpretation_id,
        }
