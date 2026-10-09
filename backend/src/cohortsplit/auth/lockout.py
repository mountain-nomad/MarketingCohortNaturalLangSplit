"""Brute-force protection: per-account failure counting and temporary lockout (FR-A1).

Keyed by normalized email so unknown accounts are throttled exactly like known ones
(responses never reveal whether an account exists).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cohortsplit.auth.models import LoginThrottle
from cohortsplit.config import Settings


@dataclass(frozen=True)
class LockoutPolicy:
    max_failures: int
    window: timedelta
    lockout: timedelta

    @classmethod
    def from_settings(cls, settings: Settings) -> "LockoutPolicy":
        return cls(
            max_failures=settings.login_max_failures,
            window=timedelta(minutes=settings.login_failure_window_minutes),
            lockout=timedelta(minutes=settings.login_lockout_minutes),
        )


def _throttle(db: Session, email: str, now: datetime) -> LoginThrottle:
    row = db.get(LoginThrottle, email, with_for_update=True)
    if row is not None:
        return row
    row = LoginThrottle(email=email, failure_count=0, updated_at=now)
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:  # concurrent first failure for the same email
        row = db.get(LoginThrottle, email, with_for_update=True, populate_existing=True)
        if row is None:  # pragma: no cover - deleted concurrently
            raise
    return row


def locked_for(db: Session, email: str, now: datetime) -> int | None:
    """Seconds until the lock ends, or None when attempts are allowed."""
    row = db.get(LoginThrottle, email)
    if row is None or row.locked_until is None or row.locked_until <= now:
        return None
    return max(1, int((row.locked_until - now).total_seconds()))


def register_failure(db: Session, email: str, now: datetime, policy: LockoutPolicy) -> bool:
    """Count one failure. Returns True when this failure starts a lockout."""
    row = _throttle(db, email, now)
    if row.window_started_at is None or now - row.window_started_at >= policy.window:
        row.failure_count = 1
        row.window_started_at = now
    else:
        row.failure_count += 1
    row.updated_at = now
    if row.failure_count >= policy.max_failures:
        row.locked_until = now + policy.lockout
        row.failure_count = 0
        row.window_started_at = None
        return True
    return False


def clear(db: Session, email: str, now: datetime) -> None:
    row = db.get(LoginThrottle, email)
    if row is not None:
        row.failure_count = 0
        row.window_started_at = None
        row.locked_until = None
        row.updated_at = now
