"""Experiment HTTP API (FR-8, FR-9, FR-9a).

* ``POST /api/runs`` (``cohort.create``) — split a prepared cohort into a stored run.
* ``GET /api/runs`` and ``GET /api/runs/{run_id}`` (``cohort.create`` or
  ``cohort.read_all``) — own runs, or everyone's with ``cohort.read_all``; a run the
  caller may not see is 404 like an unknown one.
* ``POST /api/runs/{run_id}/download`` (``cohort.export``) — the export ZIP rebuilt from
  the stored snapshot; column grants are checked on every download. POST (with CSRF)
  rather than GET because every download is an audited, warehouse-touching action.
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool

from cohortsplit.auth.clock import Clock, get_clock
from cohortsplit.auth.dependencies import (
    DbSession,
    get_request_id,
    require_any_permission,
    require_permission,
)
from cohortsplit.auth.policy import Principal
from cohortsplit.cohort.api import MAX_EXPORT_COLUMNS, Hex32
from cohortsplit.cohort.environment import CohortEnvironment, get_cohort_environment
from cohortsplit.cohort.service import RequestContext
from cohortsplit.cohort_spec.common import QualifiedColumn
from cohortsplit.experiment.assignment import Allocation, validate_experiment_key
from cohortsplit.experiment.service import ExperimentService, SplitRequest

router = APIRouter(prefix="/api/runs", tags=["experiments"])

CanCreate = Annotated[Principal, Depends(require_permission("cohort.create"))]
CanSeeHistory = Annotated[
    Principal, Depends(require_any_permission("cohort.create", "cohort.read_all"))
]
CanExport = Annotated[Principal, Depends(require_permission("cohort.export"))]
Environment = Annotated[CohortEnvironment, Depends(get_cohort_environment)]
ExperimentKey = Annotated[str, Field(max_length=200), AfterValidator(validate_experiment_key)]


@dataclass(frozen=True)
class ExperimentState:
    service: ExperimentService


def get_experiment_service(request: Request) -> ExperimentService:
    state: ExperimentState = request.app.state.experiment
    return state.service


Service = Annotated[ExperimentService, Depends(get_experiment_service)]


class SplitIn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prepared_cohort_id: Hex32
    experiment_key: ExperimentKey
    allocation: Allocation = Allocation()
    experiment_type: Literal["ab", "aa"] = "ab"
    export_columns: list[QualifiedColumn] = Field(default=[], max_length=MAX_EXPORT_COLUMNS)


class DownloadIn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # None: the columns chosen at split time; []: canonical user id only.
    columns: list[QualifiedColumn] | None = Field(default=None, max_length=MAX_EXPORT_COLUMNS)
    confirm_empty_group: StrictBool = False


def _context(request: Request, principal: Principal, clock: Clock) -> RequestContext:
    return RequestContext(principal=principal, now=clock(), request_id=get_request_id(request))


@router.post("", status_code=201)
def create_run(
    body: SplitIn,
    request: Request,
    principal: CanCreate,
    db: DbSession,
    service: Service,
    environment: Environment,
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return service.split(
        db,
        environment,
        SplitRequest(
            prepared_cohort_id=body.prepared_cohort_id,
            experiment_key=body.experiment_key,
            allocation=body.allocation,
            experiment_type=body.experiment_type,
            export_columns=list(body.export_columns),
        ),
        _context(request, principal, clock),
    )


@router.get("")
def list_runs(
    request: Request,
    principal: CanSeeHistory,
    db: DbSession,
    service: Service,
    clock: Annotated[Clock, Depends(get_clock)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    return service.list_runs(db, _context(request, principal, clock), limit=limit, offset=offset)


@router.get("/{run_id}")
def get_run(
    run_id: str,
    request: Request,
    principal: CanSeeHistory,
    db: DbSession,
    service: Service,
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return service.get_run(db, _context(request, principal, clock), run_id)


@router.post("/{run_id}/download", response_class=StreamingResponse)
def download_run(
    run_id: str,
    body: DownloadIn,
    request: Request,
    principal: CanExport,
    db: DbSession,
    service: Service,
    environment: Environment,
    clock: Annotated[Clock, Depends(get_clock)],
) -> StreamingResponse:
    export = service.download(
        db,
        environment,
        run_id,
        columns=None if body.columns is None else list(body.columns),
        confirm_empty_group=body.confirm_empty_group,
        ctx=_context(request, principal, clock),
    )
    return StreamingResponse(
        export.chunks(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
