"""Read-only audit log API (FR-A5). There is deliberately no create/update/delete route."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select

from cohortsplit.audit.models import AuditEvent
from cohortsplit.auth.dependencies import DbSession, require_permission
from cohortsplit.auth.models import User
from cohortsplit.auth.policy import Principal
from cohortsplit.auth.users import normalize_email

router = APIRouter(prefix="/api/audit", tags=["audit"])

MAX_PAGE_SIZE = 200


class AuditEventOut(BaseModel):
    id: int
    occurred_at: datetime
    actor_type: str
    actor_user_id: int | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    request_id: str | None
    metadata: dict[str, Any]


class AuditPageOut(BaseModel):
    items: list[AuditEventOut]
    total: int
    limit: int
    offset: int


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@router.get("/events", response_model=AuditPageOut)
def list_events(
    _: Annotated[Principal, Depends(require_permission("audit.read"))],
    db: DbSession,
    actor_email: Annotated[str | None, Query(max_length=320)] = None,
    actor_type: Literal["user", "cli", "anonymous"] | None = None,
    action: Annotated[str | None, Query(max_length=200)] = None,
    outcome: Literal["success", "denied", "error"] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPageOut:
    def filtered(statement: Select[Any]) -> Select[Any]:
        statement = statement.outerjoin(User, User.id == AuditEvent.actor_user_id)
        if actor_email:
            statement = statement.where(User.email == normalize_email(actor_email))
        if actor_type:
            statement = statement.where(AuditEvent.actor_type == actor_type)
        if action:
            statement = statement.where(AuditEvent.action == action)
        if outcome:
            statement = statement.where(AuditEvent.outcome == outcome)
        if since:
            statement = statement.where(AuditEvent.occurred_at >= _utc(since))
        if until:
            statement = statement.where(AuditEvent.occurred_at <= _utc(until))
        return statement

    total = db.execute(filtered(select(func.count(AuditEvent.id)))).scalar_one()
    rows = db.execute(
        filtered(select(AuditEvent, User.email))
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return AuditPageOut(
        items=[
            AuditEventOut(
                id=event.id,
                occurred_at=event.occurred_at,
                actor_type=event.actor_type,
                actor_user_id=event.actor_user_id,
                actor_email=email,
                action=event.action,
                target_type=event.target_type,
                target_id=event.target_id,
                outcome=event.outcome,
                request_id=event.request_id,
                metadata=event.metadata_,
            )
            for event, email in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
