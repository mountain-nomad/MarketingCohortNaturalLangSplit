"""Cohort HTTP API (FR-4 .. FR-7). Every route requires ``cohort.create``.

* ``POST /api/cohorts/interpret`` — interpretation (readable text, spec, spec hash, semantic
  version) / clarification / rejection; nothing is executed.
* ``POST /api/cohorts/preview`` — after confirming the spec hash and semantic version: count,
  samples, read-only SQL, NULL counts and the prepared cohort id.
"""

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from cohortsplit.auth.clock import Clock, get_clock
from cohortsplit.auth.dependencies import DbSession, get_request_id, require_permission
from cohortsplit.auth.policy import Principal
from cohortsplit.cohort.environment import CohortEnvironment, get_cohort_environment
from cohortsplit.cohort.service import CohortService, RequestContext
from cohortsplit.cohort_spec.common import QualifiedColumn

router = APIRouter(prefix="/api/cohorts", tags=["cohorts"])

CanCreate = Annotated[Principal, Depends(require_permission("cohort.create"))]
Environment = Annotated[CohortEnvironment, Depends(get_cohort_environment)]

MAX_REQUEST_LENGTH = 1000
MAX_EXPORT_COLUMNS = 50
Hex32 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Hex64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


@dataclass(frozen=True)
class CohortState:
    service: CohortService
    environment: CohortEnvironment


def get_cohort_service(request: Request) -> CohortService:
    state: CohortState = request.app.state.cohort
    return state.service


Service = Annotated[CohortService, Depends(get_cohort_service)]


class InterpretIn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nl_request: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_REQUEST_LENGTH)
    ]


class PreviewIn(BaseModel):
    """Explicit confirmation of one interpretation (FR-5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interpretation_id: Hex32
    spec_hash: Hex64
    semantic_version: Hex64
    export_columns: list[QualifiedColumn] = Field(default=[], max_length=MAX_EXPORT_COLUMNS)


def _context(request: Request, principal: Principal, clock: Clock) -> RequestContext:
    return RequestContext(principal=principal, now=clock(), request_id=get_request_id(request))


@router.post("/interpret")
def interpret(
    body: InterpretIn,
    request: Request,
    principal: CanCreate,
    db: DbSession,
    service: Service,
    environment: Environment,
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return service.interpret(db, environment, body.nl_request, _context(request, principal, clock))


@router.post("/preview")
def preview(
    body: PreviewIn,
    request: Request,
    principal: CanCreate,
    db: DbSession,
    service: Service,
    environment: Environment,
    clock: Annotated[Clock, Depends(get_clock)],
) -> dict[str, Any]:
    return service.preview(
        db,
        environment,
        interpretation_id=body.interpretation_id,
        confirmed_spec_hash=body.spec_hash,
        confirmed_version=body.semantic_version,
        export_columns=list(body.export_columns),
        ctx=_context(request, principal, clock),
    )
