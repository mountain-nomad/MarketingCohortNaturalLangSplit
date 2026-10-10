"""Semantic-context HTTP API (FR-3, FR-2 review queue). Every route is permission-gated.

| Route | Permission |
|---|---|
| ``GET /api/semantic/docs`` | ``semantic_context.read`` |
| ``GET /api/semantic/business-context`` | ``semantic_context.read`` |
| ``POST/PUT/DELETE /api/semantic/business-context[/{key}]`` | ``semantic_context.edit`` |
| ``GET /api/semantic/use-cases`` | ``semantic_context.read`` |
| ``POST .../use-cases/{id}/confirm|reject``, ``PUT .../use-cases/{id}`` | ``use_case.review`` |
| ``GET /api/semantic/version``, ``GET /api/semantic/versions`` | ``semantic_context.read`` |
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from sqlalchemy import Row
from sqlalchemy.orm import Session

from cohortsplit.audit import actions
from cohortsplit.audit.service import Actor, AuditEventIn, AuditService, Outcome
from cohortsplit.auth.clock import Clock, get_clock
from cohortsplit.auth.dependencies import (
    DbSession,
    get_audit,
    get_request_id,
    require_permission,
)
from cohortsplit.auth.errors import ApiError, ConflictError, ServiceUnavailableError
from cohortsplit.auth.policy import Principal
from cohortsplit.config import ConfigError
from cohortsplit.crawler.service import CrawlFailedError
from cohortsplit.semantic import repository as repo
from cohortsplit.semantic.context import BusinessContextIn
from cohortsplit.semantic.crawl import (
    CrawlCoordinator,
    CrawlEnvironment,
    CrawlInProgressError,
    get_crawl_environment,
)
from cohortsplit.semantic.inventory import SchemaInventory, check_definition
from cohortsplit.semantic.models import BusinessContextEntry, SemanticVersionRecord
from cohortsplit.semantic.provider import read_snapshot
from cohortsplit.semantic.service import (
    BusinessContextUpdate,
    ChangeContext,
    RejectBody,
    SemanticContextService,
    UseCaseEdit,
)
from cohortsplit.semantic.snapshot import PromptTable, parse_definition
from cohortsplit.warehouse.errors import WarehouseNotConfiguredError

router = APIRouter(prefix="/api/semantic", tags=["semantic"])

MAX_PAGE_SIZE = 200
UseCaseStatusQuery = Literal["pending_review", "confirmed", "rejected", "needs_rereview"]

CanRead = Annotated[Principal, Depends(require_permission("semantic_context.read"))]
CanEdit = Annotated[Principal, Depends(require_permission("semantic_context.edit"))]
CanReview = Annotated[Principal, Depends(require_permission("use_case.review"))]
CanCrawl = Annotated[Principal, Depends(require_permission("crawler.run"))]

crawler_router = APIRouter(prefix="/api/crawler", tags=["crawler"])
MAX_RUNS = 100


@dataclass(frozen=True)
class SemanticState:
    """Per-application semantic objects, stored on ``app.state.semantic``."""

    service: SemanticContextService
    coordinator: CrawlCoordinator
    crawl_environment: CrawlEnvironment


def get_semantic_state(request: Request) -> SemanticState:
    state: SemanticState = request.app.state.semantic
    return state


State = Annotated[SemanticState, Depends(get_semantic_state)]


def _ctx(request: Request, principal: Principal, clock: Clock) -> ChangeContext:
    return ChangeContext(user_id=principal.user_id, now=clock(), request_id=get_request_id(request))


# -- serialization -------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def run_out(row: Row[Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "status": row.status,
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "triggered_by": row.triggered_by,
        "content_hash": row.content_hash,
        "summary": dict(row.summary),
        "error": row.error,
    }


def _table_out(table: PromptTable) -> dict[str, Any]:
    return {
        "qualified_name": table.qualified_name,
        "kind": table.kind,
        "comment": table.comment,
        "primary_key": list(table.primary_key),
        "foreign_keys": [
            {
                "columns": list(fk.columns),
                "referred_table": fk.referred_table,
                "referred_columns": list(fk.referred_columns),
            }
            for fk in table.foreign_keys
        ],
        "estimated_row_count": table.estimated_row_count,
        "columns": [
            {
                "name": c.name,
                "data_type": c.data_type,
                "nullable": c.nullable,
                "comment": c.comment,
                "allowed_values": list(c.allowed_values) if c.allowed_values is not None else None,
                "sample_values": list(c.sample_values),
            }
            for c in table.columns
        ],
    }


def _entry_out(
    entry: BusinessContextEntry,
    users: dict[int, dict[str, Any]],
    inventory: SchemaInventory | None,
) -> dict[str, Any]:
    definition = parse_definition(entry.definition)
    if definition is None:
        missing = [{"reference": entry.kind, "problem": "definition no longer valid"}]
    elif inventory is None:
        missing = []
    else:
        missing = [
            {"reference": p.reference, "problem": p.problem}
            for p in check_definition(definition, inventory)
        ]
    return {
        "key": entry.key,
        "kind": entry.kind,
        "synonyms": list(entry.synonyms),
        "description": entry.description,
        "definition": dict(entry.definition),
        "missing_references": missing,
        "created_at": _iso(entry.created_at),
        "updated_at": _iso(entry.updated_at),
        "created_by": users.get(entry.created_by) if entry.created_by else None,
        "updated_by": users.get(entry.updated_by) if entry.updated_by else None,
    }


def _entries_out(db: Session, entries: list[BusinessContextEntry]) -> list[dict[str, Any]]:
    users = repo.user_refs(db, [e.created_by for e in entries] + [e.updated_by for e in entries])
    inventory = repo.schema_inventory(repo.generated_docs(db))
    return [_entry_out(e, users, inventory) for e in entries]


def _use_cases_out(
    db: Session, rows: list[Row[Any]], service: SemanticContextService
) -> list[dict[str, Any]]:
    users = repo.user_refs(db, [r.reviewed_by for r in rows])
    # Literals today's sampling policy forbids are never shown (ruling S7).
    withheld = service.withheld(db, rows)
    return [
        {
            "id": r.id,
            "key": r.use_case_key,
            "origin": r.origin,
            "status": r.status,
            "withheld": withheld.get(r.id),
            "nl_request": None if r.id in withheld else r.nl_request,
            "spec": None if r.id in withheld else dict(r.spec),
            "spec_version": r.spec_version,
            "template_key": r.template_key,
            "rewritten_from": r.rewritten_from,
            "generation_note": r.generation_note,
            "review_note": r.review_note,
            "referenced_columns": list(r.referenced_columns),
            "reviewed_by": users.get(r.reviewed_by) if r.reviewed_by else None,
            "reviewed_at": _iso(r.reviewed_at),
            "updated_at": _iso(r.updated_at),
        }
        for r in rows
    ]


def _use_case_out(db: Session, use_case_id: int, service: SemanticContextService) -> dict[str, Any]:
    row = repo.get_use_case(db, use_case_id)
    assert row is not None  # noqa: S101 (just written in this request)
    return _use_cases_out(db, [row], service)[0]


def _version_record_out(
    record: SemanticVersionRecord, users: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    return {
        "id": record.id,
        "version": record.version,
        "components": dict(record.components),
        "cause": record.cause,
        "target": record.target,
        "actor_type": record.actor_type,
        "actor": users.get(record.actor_user_id) if record.actor_user_id else None,
        "created_at": _iso(record.created_at),
    }


# -- generated docs ------------------------------------------------------------------------


@router.get("/docs")
def generated_docs(_: CanRead, db: DbSession, state: State) -> dict[str, Any]:
    """Generated schema docs with row counts and the samples the current policy permits."""
    snapshot = read_snapshot(db, state.service.sampling_policy(db))
    return {
        "tables": [_table_out(t) for t in snapshot.tables],
        "latest_run": run_out(repo.latest_run(db)),
    }


# -- business context ----------------------------------------------------------------------


@router.get("/business-context")
def list_business_context(_: CanRead, db: DbSession) -> dict[str, Any]:
    return {"items": _entries_out(db, repo.list_entries(db))}


@router.post("/business-context", status_code=201)
def create_business_context(
    body: BusinessContextIn,
    request: Request,
    principal: CanEdit,
    db: DbSession,
    state: State,
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    entry = state.service.create_entry(db, body, _ctx(request, principal, clock))
    db.commit()
    return _entries_out(db, [entry])[0]


@router.put("/business-context/{key}")
def update_business_context(
    key: str,
    body: BusinessContextUpdate,
    request: Request,
    principal: CanEdit,
    db: DbSession,
    state: State,
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    entry = state.service.update_entry(db, key, body, _ctx(request, principal, clock))
    db.commit()
    return _entries_out(db, [entry])[0]


@router.delete("/business-context/{key}", status_code=204)
def delete_business_context(
    key: str,
    request: Request,
    principal: CanEdit,
    db: DbSession,
    state: State,
    clock: Annotated[Clock, Depends(get_clock)],
) -> Response:
    state.service.delete_entry(db, key, _ctx(request, principal, clock))
    db.commit()
    return Response(status_code=204)


# -- use-case review -----------------------------------------------------------------------


@router.get("/use-cases")
def list_use_cases(
    _: CanRead, db: DbSession, state: State, status: UseCaseStatusQuery | None = None
) -> dict[str, Any]:
    return {"items": _use_cases_out(db, repo.list_use_cases(db, status), state.service)}


@router.post("/use-cases/{use_case_id}/confirm")
def confirm_use_case(
    use_case_id: int,
    request: Request,
    principal: CanReview,
    db: DbSession,
    state: State,
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    state.service.confirm(db, use_case_id, _ctx(request, principal, clock))
    db.commit()
    return _use_case_out(db, use_case_id, state.service)


@router.post("/use-cases/{use_case_id}/reject")
def reject_use_case(
    use_case_id: int,
    request: Request,
    principal: CanReview,
    db: DbSession,
    state: State,
    clock: Annotated[Clock, Depends(get_clock)],
    body: Annotated[RejectBody | None, Body()] = None,
) -> dict[str, Any]:
    note = body.note if body is not None else None
    state.service.reject(db, use_case_id, note, _ctx(request, principal, clock))
    db.commit()
    return _use_case_out(db, use_case_id, state.service)


@router.put("/use-cases/{use_case_id}")
def edit_use_case(
    use_case_id: int,
    body: UseCaseEdit,
    request: Request,
    principal: CanReview,
    db: DbSession,
    state: State,
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    state.service.edit(db, use_case_id, body, _ctx(request, principal, clock))
    db.commit()
    return _use_case_out(db, use_case_id, state.service)


# -- semantic version ----------------------------------------------------------------------


@router.get("/version")
def semantic_version(_: CanRead, db: DbSession, state: State) -> dict[str, Any]:
    current = state.service.current_version(db)
    return {"version": current.version, "components": current.components()}


@router.get("/versions")
def semantic_versions(
    _: CanRead,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    items, total = repo.version_history(db, limit=limit, offset=offset)
    users = repo.user_refs(db, [i.actor_user_id for i in items])
    return {
        "items": [_version_record_out(i, users) for i in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# -- crawler -------------------------------------------------------------------------------


@crawler_router.post("/runs", status_code=201)
def start_crawl(
    request: Request,
    principal: CanCrawl,
    db: DbSession,
    state: State,
    environment: Annotated[CrawlEnvironment, Depends(get_crawl_environment)],
    audit: Annotated[AuditService, Depends(get_audit)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    """Crawl now (synchronously). One crawl at a time; human content is never overwritten."""
    actor = Actor.user(principal.user_id)

    def start_event(outcome: Outcome, metadata: dict[str, object]) -> AuditEventIn:
        return AuditEventIn(
            action=actions.CRAWLER_START,
            outcome=outcome,
            actor=actor,
            occurred_at=clock(),
            target_type="crawler",
            target_id=None,
            request_id=get_request_id(request),
            metadata=metadata,
        )

    def on_start() -> None:
        # Fail closed: no crawl without its audit record (AuditWriteError -> 503).
        audit.record_detached(start_event("success", {}), sensitive=True)

    try:
        outcome = state.coordinator.run(
            environment,
            actor=actor,
            triggered_by=f"user:{principal.user_id}",
            now=clock,
            on_start=on_start,
        )
    except WarehouseNotConfiguredError as exc:
        raise ServiceUnavailableError("warehouse_not_configured", str(exc)) from None
    except ConfigError as exc:
        raise ServiceUnavailableError("crawler_misconfigured", str(exc)) from None
    except CrawlInProgressError:
        audit.record_detached(
            start_event("denied", {"reason": "crawl_in_progress"}), sensitive=False
        )
        raise ConflictError(
            "crawl_in_progress", "Another crawl is running. Try again when it has finished."
        ) from None
    except CrawlFailedError as exc:
        raise ApiError(502, "crawl_failed", str(exc), extra={"run_id": exc.run_id}) from None

    run = run_out(repo.get_run(db, outcome.report.run_id))
    assert run is not None  # noqa: S101 (just recorded)
    return {**run, "semantic_version": outcome.semantic_version.version}


@crawler_router.get("/runs")
def list_crawl_runs(
    _: CanRead,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_RUNS)] = 20,
) -> dict[str, Any]:
    return {"items": [run_out(r) for r in repo.list_runs(db, limit)]}
