"""Login, logout and password change (FR-A1, FR-A2)."""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from cohortsplit.audit import actions
from cohortsplit.audit.service import Actor, AuditEventIn, AuditService
from cohortsplit.auth import lockout, passwords
from cohortsplit.auth.errors import (
    ApiError,
    InvalidCredentialsError,
    LoginLockedError,
    UnprocessableError,
)
from cohortsplit.auth.models import User
from cohortsplit.auth.sessions import (
    IssuedSession,
    create_session,
    revoke_session,
    revoke_user_sessions,
)
from cohortsplit.auth.users import find_by_email, normalize_email
from cohortsplit.config import Settings

# Longer inputs cannot be valid passwords (policy max is 128); skip hashing them.
_MAX_VERIFIABLE_LENGTH = 1024


@dataclass(frozen=True)
class LoginResult:
    user: User
    session: IssuedSession


def _target(user: User | None, email: str) -> tuple[str, str | None, dict[str, object]]:
    if user is not None:
        return "user", str(user.id), {}
    # Never store the raw input: people sometimes type a password into the email field.
    fingerprint = hashlib.sha256(email.encode()).hexdigest()[:16]
    return "account", None, {"email_fingerprint": fingerprint}


class AuthService:
    def __init__(self, settings: Settings, audit: AuditService) -> None:
        self._settings = settings
        self._audit = audit
        self._lockout = lockout.LockoutPolicy.from_settings(settings)

    def _record(self, db: Session, event: AuditEventIn, *, sensitive: bool) -> None:
        self._audit.record(db, event, sensitive=sensitive)

    def login(
        self, db: Session, *, email: str, password: str, now: datetime, request_id: str | None
    ) -> LoginResult:
        normalized = normalize_email(email)
        user = find_by_email(db, normalized)
        target_type, target_id, extra = _target(user, normalized)

        def failure_event(reason: str) -> AuditEventIn:
            return AuditEventIn(
                action=actions.LOGIN_FAILED,
                outcome="denied",
                actor=Actor.anonymous(),
                occurred_at=now,
                target_type=target_type,
                target_id=target_id,
                request_id=request_id,
                metadata={"reason": reason, **extra},
            )

        remaining = lockout.locked_for(db, normalized, now)
        if remaining is not None:
            self._record(db, failure_event("locked"), sensitive=False)
            db.commit()
            raise LoginLockedError(remaining)

        verified = False
        if len(password) <= _MAX_VERIFIABLE_LENGTH:
            if user is not None:
                verified = passwords.verify_password(user.password_hash, password)
            else:
                passwords.verify_against_dummy(password)

        if user is not None and user.is_active and verified:
            lockout.clear(db, normalized, now)
            user.last_login_at = now
            issued = create_session(db, user.id, now, self._settings)
            self._record(
                db,
                AuditEventIn(
                    action=actions.LOGIN,
                    outcome="success",
                    actor=Actor.user(user.id),
                    occurred_at=now,
                    target_type="user",
                    target_id=str(user.id),
                    request_id=request_id,
                    metadata={"session_id": issued.session_id},
                ),
                sensitive=False,
            )
            db.commit()
            return LoginResult(user, issued)

        reason = "inactive" if user is not None and verified else "invalid_credentials"
        locked_now = lockout.register_failure(db, normalized, now, self._lockout)
        self._record(db, failure_event(reason), sensitive=False)
        if locked_now:
            self._record(
                db,
                AuditEventIn(
                    action=actions.LOCKOUT,
                    outcome="denied",
                    actor=Actor.anonymous(),
                    occurred_at=now,
                    target_type=target_type,
                    target_id=target_id,
                    request_id=request_id,
                    metadata={
                        "lockout_minutes": self._settings.login_lockout_minutes,
                        **extra,
                    },
                ),
                sensitive=False,
            )
        db.commit()
        raise InvalidCredentialsError()

    def logout(
        self,
        db: Session,
        *,
        user_id: int,
        session_id: int,
        now: datetime,
        request_id: str | None,
    ) -> None:
        revoke_session(db, session_id, now)
        self._record(
            db,
            AuditEventIn(
                action=actions.LOGOUT,
                outcome="success",
                actor=Actor.user(user_id),
                occurred_at=now,
                target_type="user",
                target_id=str(user_id),
                request_id=request_id,
                metadata={"session_id": session_id},
            ),
            sensitive=False,
        )
        db.commit()

    def change_password(
        self,
        db: Session,
        *,
        user: User,
        session_id: int,
        current_password: str,
        new_password: str,
        now: datetime,
        request_id: str | None,
    ) -> None:
        forced = user.must_change_password
        action = actions.PASSWORD_CHANGE_FORCED if forced else actions.PASSWORD_CHANGE

        def event(outcome: str, **metadata: object) -> AuditEventIn:
            return AuditEventIn(
                action=action,
                outcome="success" if outcome == "success" else "denied",
                actor=Actor.user(user.id),
                occurred_at=now,
                target_type="user",
                target_id=str(user.id),
                request_id=request_id,
                metadata=metadata,
            )

        remaining = lockout.locked_for(db, user.email, now)
        if remaining is not None:
            self._record(db, event("denied", reason="locked"), sensitive=False)
            db.commit()
            raise LoginLockedError(remaining)

        if len(current_password) > _MAX_VERIFIABLE_LENGTH or not passwords.verify_password(
            user.password_hash, current_password
        ):
            locked_now = lockout.register_failure(db, user.email, now, self._lockout)
            self._record(db, event("denied", reason="invalid_current_password"), sensitive=False)
            if locked_now:
                self._record(
                    db,
                    AuditEventIn(
                        action=actions.LOCKOUT,
                        outcome="denied",
                        actor=Actor.user(user.id),
                        occurred_at=now,
                        target_type="user",
                        target_id=str(user.id),
                        request_id=request_id,
                        metadata={"lockout_minutes": self._settings.login_lockout_minutes},
                    ),
                    sensitive=False,
                )
            db.commit()
            raise ApiError(400, "invalid_current_password", "Current password is incorrect.")

        try:
            passwords.validate_password_policy(new_password)
        except passwords.PasswordPolicyError as exc:
            raise UnprocessableError("password_policy", str(exc)) from None
        if passwords.verify_password(user.password_hash, new_password):
            raise UnprocessableError(
                "password_reuse", "The new password must differ from the current one."
            )

        user.password_hash = passwords.hash_password(new_password)
        user.must_change_password = False
        user.updated_at = now
        revoke_user_sessions(db, user.id, now, except_session_id=session_id)
        lockout.clear(db, user.email, now)
        # Credential change: fail closed if it cannot be audited.
        self._record(db, event("success"), sensitive=True)
        db.commit()
