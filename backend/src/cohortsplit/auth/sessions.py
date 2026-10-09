"""Server-side sessions (FR-A1).

The browser holds a random 256-bit session token (HttpOnly cookie) and a separate CSRF
token. The database stores only SHA-256 digests of both, so a leaked table does not
yield usable sessions.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cohortsplit.auth.models import AuthSession, User
from cohortsplit.config import Settings

# Refresh last_seen_at at most this often (fewer writes; idle timeout is in hours).
LAST_SEEN_RESOLUTION = timedelta(minutes=1)


@dataclass(frozen=True)
class IssuedSession:
    session_id: int
    token: str
    csrf_token: str
    expires_at: datetime


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user_id: int, now: datetime, settings: Settings) -> IssuedSession:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(hours=settings.session_max_lifetime_hours)
    row = AuthSession(
        token_hash=digest(token),
        csrf_token_hash=digest(csrf_token),
        user_id=user_id,
        created_at=now,
        last_seen_at=now,
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    return IssuedSession(row.id, token, csrf_token, expires_at)


def resolve_session(
    db: Session, token: str, now: datetime, settings: Settings
) -> tuple[AuthSession, User] | None:
    """Return the live session and its active user, or None. Refreshes ``last_seen_at``."""
    row = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == digest(token))
    ).one_or_none()
    if row is None:
        return None
    session, user = row[0], row[1]
    idle_limit = timedelta(minutes=settings.session_idle_timeout_minutes)
    if (
        session.revoked_at is not None
        or now >= session.expires_at
        or now - session.last_seen_at >= idle_limit
        or not user.is_active
    ):
        return None
    if now - session.last_seen_at >= LAST_SEEN_RESOLUTION:
        session.last_seen_at = now
        db.commit()
    return session, user


def csrf_matches(session: AuthSession, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(session.csrf_token_hash, digest(presented))


def revoke_session(db: Session, session_id: int, now: datetime) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def revoke_user_sessions(
    db: Session, user_id: int, now: datetime, *, except_session_id: int | None = None
) -> None:
    statement = update(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    )
    if except_session_id is not None:
        statement = statement.where(AuthSession.id != except_session_id)
    db.execute(statement.values(revoked_at=now))
