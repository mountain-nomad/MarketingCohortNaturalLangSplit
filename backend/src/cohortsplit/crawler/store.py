"""Persistence of crawler output in appdb.

Re-run rules (FR-2, AC-25 crawler half, AC-26):
- generated docs are replaced wholesale; human docs are never touched;
- generated ``pending_review`` use cases are replaced; generated use cases a human
  already reviewed (confirmed / rejected / needs_rereview) are kept and their key is
  not regenerated; human use cases are never touched, and a template key held by a
  human row (a reviewer edited the generated suggestion) is not regenerated either;
- confirmed use cases (any origin) that reference a table/column that no longer
  exists in the crawled scope become ``needs_rereview`` with a note naming what is
  missing; their spec is left unchanged;
- all of the above happens in ONE transaction, so a failure leaves previous content intact.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, Row, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cohortsplit.cohort_spec.draft import DraftCohortSpec, referenced_columns
from cohortsplit.crawler.catalog import WarehouseCatalog
from cohortsplit.crawler.tables import crawl_runs, example_use_cases, semantic_docs
from cohortsplit.crawler.use_cases import GeneratedUseCase, UseCaseGeneration, UseCaseStatus

# Serializes concurrent swaps (transaction-scoped advisory lock). The semantic-context
# service takes the same lock for edits and review decisions, so swaps, edits and the
# semantic version history are serialized.
_SWAP_LOCK_KEY = 0x435253574150  # "CRSWAP"
SEMANTIC_CONTENT_LOCK_KEY = _SWAP_LOCK_KEY


@dataclass(frozen=True)
class CrawlRun:
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    triggered_by: str | None
    content_hash: str | None
    summary: dict[str, Any]
    error: str | None


@dataclass(frozen=True)
class StoredDoc:
    id: int
    doc_key: str
    kind: str
    origin: str
    content: dict[str, Any]
    crawl_run_id: int | None
    updated_at: datetime


@dataclass(frozen=True)
class StoredUseCase:
    id: int
    use_case_key: str
    origin: str
    status: str
    nl_request: str
    spec: dict[str, Any]
    spec_version: str
    template_key: str | None
    rewritten_from: str | None
    generation_note: str | None
    review_note: str | None
    referenced_columns: tuple[str, ...]
    crawl_run_id: int | None
    updated_at: datetime
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None


@dataclass(frozen=True)
class FlaggedUseCase:
    id: int
    nl_request: str
    missing: tuple[str, ...]


@dataclass(frozen=True)
class SwapResult:
    inserted_use_cases: int
    preserved_keys: tuple[str, ...]
    flagged: tuple[FlaggedUseCase, ...]


def table_doc_key(qualified_table: str) -> str:
    return f"table:{qualified_table}"


def _run(row: Row[Any]) -> CrawlRun:
    return CrawlRun(
        id=row.id,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        triggered_by=row.triggered_by,
        content_hash=row.content_hash,
        summary=dict(row.summary),
        error=row.error,
    )


def _doc(row: Row[Any]) -> StoredDoc:
    return StoredDoc(
        id=row.id,
        doc_key=row.doc_key,
        kind=row.kind,
        origin=row.origin,
        content=dict(row.content),
        crawl_run_id=row.crawl_run_id,
        updated_at=row.updated_at,
    )


def _use_case(row: Row[Any]) -> StoredUseCase:
    return StoredUseCase(
        id=row.id,
        use_case_key=row.use_case_key,
        origin=row.origin,
        status=row.status,
        nl_request=row.nl_request,
        spec=dict(row.spec),
        spec_version=row.spec_version,
        template_key=row.template_key,
        rewritten_from=row.rewritten_from,
        generation_note=row.generation_note,
        review_note=row.review_note,
        referenced_columns=tuple(row.referenced_columns),
        crawl_run_id=row.crawl_run_id,
        updated_at=row.updated_at,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
    )


def _missing_references(
    columns: Sequence[str],
    catalog: WarehouseCatalog,
    scope_schemas: frozenset[str] | None,
) -> tuple[str, ...]:
    """References that no longer exist, limited to the crawled schemas."""
    known_columns = catalog.qualified_columns()
    known_tables = catalog.qualified_tables()
    missing: list[str] = []
    for column in sorted(set(columns)):
        table = column.rsplit(".", 1)[0]
        if scope_schemas is not None and table.split(".", 1)[0] not in scope_schemas:
            continue
        if table not in known_tables:
            label = f"table {table}"
            if label not in missing:
                missing.append(label)
        elif column not in known_columns:
            missing.append(f"column {column}")
    return tuple(missing)


AfterSwap = Callable[[Connection, int], None]


class CrawlStore:
    def __init__(self, engine: Engine, *, after_swap: AfterSwap | None = None) -> None:
        """``after_swap(conn, run_id)`` runs inside every swap transaction, under the swap
        lock, after the new content is written (e.g. to record the semantic version). If
        it raises, the whole swap rolls back and the crawl fails."""
        self._engine = engine
        self._after_swap = after_swap

    # -- runs ------------------------------------------------------------------------------

    def start_run(self, triggered_by: str | None) -> int:
        with self._engine.begin() as conn:
            run_id: int = conn.execute(
                crawl_runs.insert()
                .values(status="running", triggered_by=triggered_by)
                .returning(crawl_runs.c.id)
            ).scalar_one()
        return run_id

    def mark_run_failed(self, run_id: int, error: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(crawl_runs)
                .where(crawl_runs.c.id == run_id)
                .values(status="failed", finished_at=func.now(), error=error, content_hash=None)
            )

    def get_run(self, run_id: int) -> CrawlRun | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(crawl_runs).where(crawl_runs.c.id == run_id)).first()
        return None if row is None else _run(row)

    def latest_run(self) -> CrawlRun | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(crawl_runs).order_by(crawl_runs.c.id.desc()).limit(1)).first()
        return None if row is None else _run(row)

    # -- the transactional swap -----------------------------------------------------------

    def swap_generated_content(
        self,
        run_id: int,
        *,
        catalog: WarehouseCatalog,
        generation: UseCaseGeneration,
        content_hash: str,
        summary: dict[str, Any],
        scope_schemas: frozenset[str] | None,
    ) -> SwapResult:
        """Replace generated content atomically. ``scope_schemas=None`` = full crawl."""
        with self._engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SWAP_LOCK_KEY})

            # A scoped crawl only replaces generated docs of the schemas it crawled.
            stale_docs = delete(semantic_docs).where(semantic_docs.c.origin == "generated")
            if scope_schemas is not None:
                stale_docs = stale_docs.where(
                    or_(
                        *(
                            semantic_docs.c.doc_key.startswith(
                                table_doc_key(f"{schema}."), autoescape=True
                            )
                            for schema in sorted(scope_schemas)
                        )
                    )
                )
            conn.execute(stale_docs)
            self._insert_docs(conn, run_id, catalog)

            # Keys a human already acted on: reviewed generated rows, and human rows (an
            # edited suggestion keeps its template key and becomes origin=human).
            reviewed = set(
                conn.execute(
                    select(example_use_cases.c.use_case_key).where(
                        example_use_cases.c.origin == "generated",
                        example_use_cases.c.status != "pending_review",
                    )
                ).scalars()
            )
            generated_keys = {u.key for u in generation.use_cases}
            edited = set(
                conn.execute(
                    select(example_use_cases.c.use_case_key).where(
                        example_use_cases.c.origin == "human",
                        example_use_cases.c.use_case_key.in_(sorted(generated_keys)),
                    )
                ).scalars()
            )
            preserved = reviewed | edited
            conn.execute(
                delete(example_use_cases).where(
                    example_use_cases.c.origin == "generated",
                    example_use_cases.c.status == "pending_review",
                )
            )
            fresh = [u for u in generation.use_cases if u.key not in preserved]
            self._insert_use_cases(conn, run_id, fresh)

            flagged = self._flag_stale_confirmed(conn, run_id, catalog, scope_schemas)
            preserved_keys = tuple(sorted(preserved))
            conn.execute(
                update(crawl_runs)
                .where(crawl_runs.c.id == run_id)
                .values(
                    status="succeeded",
                    finished_at=func.now(),
                    content_hash=content_hash,
                    error=None,
                    summary={
                        **summary,
                        "inserted_use_cases": len(fresh),
                        "preserved_keys": list(preserved_keys),
                        "flagged": [
                            {"id": f.id, "nl_request": f.nl_request, "missing": list(f.missing)}
                            for f in flagged
                        ],
                    },
                )
            )
            if self._after_swap is not None:
                self._after_swap(conn, run_id)
        return SwapResult(
            inserted_use_cases=len(fresh), preserved_keys=preserved_keys, flagged=flagged
        )

    def _insert_docs(self, conn: Connection, run_id: int, catalog: WarehouseCatalog) -> None:
        rows: list[dict[str, Any]] = []
        for table in catalog.tables:
            rows.append(
                {
                    "doc_key": table_doc_key(table.qualified_name),
                    "kind": "table_schema",
                    "origin": "generated",
                    "content": table.model_dump(mode="json"),
                    "crawl_run_id": run_id,
                }
            )
        for profile in catalog.profiles:
            rows.append(
                {
                    "doc_key": table_doc_key(profile.table),
                    "kind": "data_profile",
                    "origin": "generated",
                    "content": profile.model_dump(mode="json"),
                    "crawl_run_id": run_id,
                }
            )
        if rows:
            conn.execute(semantic_docs.insert(), rows)

    def _insert_use_cases(
        self, conn: Connection, run_id: int, use_cases: Sequence[GeneratedUseCase]
    ) -> None:
        if not use_cases:
            return
        conn.execute(
            example_use_cases.insert(),
            [
                {
                    "use_case_key": u.key,
                    "origin": "generated",
                    "status": u.status,
                    "nl_request": u.nl_request,
                    "spec": u.spec.model_dump(mode="json"),
                    "spec_version": u.spec.spec_version,
                    "template_key": u.template_key,
                    "rewritten_from": u.rewritten_from,
                    "generation_note": u.generation_note,
                    "referenced_columns": sorted(referenced_columns(u.spec)),
                    "crawl_run_id": run_id,
                }
                for u in use_cases
            ],
        )

    def _flag_stale_confirmed(
        self,
        conn: Connection,
        run_id: int,
        catalog: WarehouseCatalog,
        scope_schemas: frozenset[str] | None,
    ) -> tuple[FlaggedUseCase, ...]:
        confirmed = conn.execute(
            select(
                example_use_cases.c.id,
                example_use_cases.c.nl_request,
                example_use_cases.c.referenced_columns,
            )
            .where(example_use_cases.c.status == "confirmed")
            .order_by(example_use_cases.c.id)
        ).all()
        flagged: list[FlaggedUseCase] = []
        for row in confirmed:
            missing = _missing_references(list(row.referenced_columns), catalog, scope_schemas)
            if not missing:
                continue
            note = (
                f"Flagged by crawl run {run_id}: no longer in the warehouse: "
                f"{', '.join(missing)}. Review and edit or reject this use case."
            )
            conn.execute(
                update(example_use_cases)
                .where(example_use_cases.c.id == row.id)
                .values(status="needs_rereview", review_note=note, updated_at=func.now())
            )
            flagged.append(FlaggedUseCase(id=row.id, nl_request=row.nl_request, missing=missing))
        return tuple(flagged)

    # -- reads -----------------------------------------------------------------------------

    def list_docs(self, *, origin: str | None = None, kind: str | None = None) -> list[StoredDoc]:
        query = select(semantic_docs).order_by(semantic_docs.c.id)
        if origin is not None:
            query = query.where(semantic_docs.c.origin == origin)
        if kind is not None:
            query = query.where(semantic_docs.c.kind == kind)
        with self._engine.connect() as conn:
            return [_doc(row) for row in conn.execute(query)]

    def list_use_cases(
        self, *, origin: str | None = None, status: str | None = None
    ) -> list[StoredUseCase]:
        query = select(example_use_cases).order_by(example_use_cases.c.id)
        if origin is not None:
            query = query.where(example_use_cases.c.origin == origin)
        if status is not None:
            query = query.where(example_use_cases.c.status == status)
        with self._engine.connect() as conn:
            return [_use_case(row) for row in conn.execute(query)]

    def list_confirmed_use_cases(self) -> list[StoredUseCase]:
        """The only use cases allowed to influence interpretation (BR-7)."""
        return self.list_use_cases(status="confirmed")

    # -- human content and review (used by the semantic-context branch) --------------------

    def add_human_use_case(
        self, nl_request: str, spec: DraftCohortSpec, status: UseCaseStatus = "pending_review"
    ) -> int:
        with self._engine.begin() as conn:
            use_case_id: int = conn.execute(
                example_use_cases.insert()
                .values(
                    use_case_key=f"human:{uuid.uuid4().hex}",
                    origin="human",
                    status=status,
                    nl_request=nl_request,
                    spec=spec.model_dump(mode="json"),
                    spec_version=spec.spec_version,
                    referenced_columns=sorted(referenced_columns(spec)),
                )
                .returning(example_use_cases.c.id)
            ).scalar_one()
        return use_case_id

    def upsert_human_doc(self, doc_key: str, kind: str, content: dict[str, Any]) -> int:
        insert = pg_insert(semantic_docs).values(
            doc_key=doc_key, kind=kind, origin="human", content=content
        )
        upsert = insert.on_conflict_do_update(
            constraint="semantic_docs_key_kind_origin_key",
            set_={"content": insert.excluded.content, "updated_at": func.now()},
        ).returning(semantic_docs.c.id)
        with self._engine.begin() as conn:
            doc_id: int = conn.execute(upsert).scalar_one()
        return doc_id

    def set_use_case_status(
        self, use_case_id: int, status: UseCaseStatus, review_note: str | None = None
    ) -> None:
        with self._engine.begin() as conn:
            result = conn.execute(
                update(example_use_cases)
                .where(example_use_cases.c.id == use_case_id)
                .values(status=status, review_note=review_note, updated_at=func.now())
            )
            if result.rowcount != 1:
                raise LookupError(f"example use case {use_case_id} does not exist")
