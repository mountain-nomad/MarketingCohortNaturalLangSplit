"""Audit service: the only writer of ``audit_events`` (append-only, FR-A5).

Two write modes:

* :meth:`AuditService.record` writes in the caller's transaction, so a state change and
  its audit event commit or roll back together. Sensitive events fail closed: a failed
  write raises :class:`AuditWriteError` and the caller's change must not commit.
* :meth:`AuditService.record_detached` writes in its own transaction and commits at once
  (denials, export authorizations: the event must persist even though the request fails
  or no other change is committed).

Events never carry passwords, tokens, credentials or exported row values. Metadata keys
that look secret are redacted as a last line of defence.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.audit.models import AuditEvent

logger = logging.getLogger(__name__)

ActorType = Literal["user", "cli", "anonymous"]
Outcome = Literal["success", "denied", "error"]

# Not "hash": cohort runs legitimately record spec/SQL hashes.
_SECRET_KEY_MARKERS = ("password", "token", "secret", "credential", "csrf", "dsn", "cookie")
REDACTED = "[redacted]"


class AuditWriteError(Exception):
    """A sensitive audit event could not be written; the action must be refused."""


@dataclass(frozen=True)
class Actor:
    type: ActorType
    user_id: int | None = None

    @classmethod
    def user(cls, user_id: int) -> "Actor":
        return cls("user", user_id)

    @classmethod
    def cli(cls) -> "Actor":
        return cls("cli")

    @classmethod
    def anonymous(cls) -> "Actor":
        return cls("anonymous")


@dataclass(frozen=True)
class AuditEventIn:
    action: str
    outcome: Outcome
    actor: Actor
    occurred_at: datetime
    target_type: str | None = None
    target_id: str | None = None
    request_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


def _scrub(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(k): (REDACTED if _looks_secret(str(k)) else _scrub(v)) for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value, key=str) if isinstance(value, set | frozenset) else value
        return [_scrub(v) for v in items]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def _to_row(event: AuditEventIn) -> AuditEvent:
    metadata = {
        str(k): (REDACTED if _looks_secret(str(k)) else _scrub(v))
        for k, v in event.metadata.items()
    }
    return AuditEvent(
        occurred_at=event.occurred_at,
        actor_type=event.actor.type,
        actor_user_id=event.actor.user_id,
        action=event.action,
        target_type=event.target_type,
        target_id=event.target_id,
        outcome=event.outcome,
        request_id=event.request_id,
        metadata_=metadata,
    )


class AuditService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(self, db: Session, event: AuditEventIn, *, sensitive: bool = True) -> None:
        """Write ``event`` in ``db``'s current transaction."""
        if sensitive:
            try:
                db.add(_to_row(event))
                db.flush()
            except SQLAlchemyError as exc:
                logger.error("audit write failed action=%s (sensitive; refusing)", event.action)
                raise AuditWriteError(event.action) from exc
            return
        try:
            with db.begin_nested():
                db.add(_to_row(event))
        except SQLAlchemyError:
            logger.warning("audit write failed action=%s (non-sensitive; continuing)", event.action)

    def record_detached(self, event: AuditEventIn, *, sensitive: bool) -> None:
        """Write ``event`` in its own transaction and commit immediately."""
        try:
            with self._session_factory() as db, db.begin():
                db.add(_to_row(event))
        except SQLAlchemyError as exc:
            if sensitive:
                logger.error("audit write failed action=%s (sensitive; refusing)", event.action)
                raise AuditWriteError(event.action) from exc
            logger.warning("audit write failed action=%s (non-sensitive; continuing)", event.action)
