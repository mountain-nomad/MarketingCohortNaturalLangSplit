"""Shared context for audited administrative changes."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from cohortsplit.audit.service import Actor, AuditEventIn, AuditService, Outcome
from cohortsplit.auth.errors import ApiError


@dataclass(frozen=True)
class ChangeContext:
    """Who changes what, when; every change is audited in the same transaction."""

    audit: AuditService
    actor: Actor
    now: datetime
    request_id: str | None

    def record(
        self,
        db: Session,
        action: str,
        *,
        target_type: str,
        target_id: object,
        metadata: dict[str, object] | None = None,
        outcome: Outcome = "success",
    ) -> None:
        """Sensitive by default: raises AuditWriteError, so the caller's change rolls back."""
        self.audit.record(
            db,
            AuditEventIn(
                action=action,
                outcome=outcome,
                actor=self.actor,
                occurred_at=self.now,
                target_type=target_type,
                target_id=str(target_id),
                request_id=self.request_id,
                metadata=metadata or {},
            ),
        )

    def deny(
        self,
        action: str,
        error: ApiError,
        *,
        target_type: str,
        target_id: object,
        **metadata: object,
    ) -> ApiError:
        """Audit a refused change in its own transaction (it survives the rollback).

        Best effort: the refusal stands even if the audit store is down. Returns ``error``
        for the caller to raise.
        """
        self.audit.record_detached(
            AuditEventIn(
                action=action,
                outcome="denied",
                actor=self.actor,
                occurred_at=self.now,
                target_type=target_type,
                target_id=str(target_id),
                request_id=self.request_id,
                metadata={"reason": error.code, **metadata},
            ),
            sensitive=False,
        )
        return error
