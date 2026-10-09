"""User account operations shared by the admin API and the operator CLI."""

import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cohortsplit.audit import actions
from cohortsplit.audit.service import Actor, AuditEventIn, AuditService
from cohortsplit.auth import lockout, passwords
from cohortsplit.auth.models import Role, User, UserRole
from cohortsplit.auth.sessions import revoke_user_sessions

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 254


class InvalidEmailError(ValueError):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> str:
    """Normalize and validate; raises InvalidEmailError (message never echoes input)."""
    normalized = normalize_email(email)
    if len(normalized) > MAX_EMAIL_LENGTH or not _EMAIL_RE.fullmatch(normalized):
        raise InvalidEmailError("Enter a valid email address.")
    return normalized


def find_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == normalize_email(email))).scalar_one_or_none()


def admin_role(db: Session, *, for_update: bool = False) -> Role:
    statement = select(Role).where(Role.is_system.is_(True))
    if for_update:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one()


def count_active_admins(db: Session, admin_role_id: int) -> int:
    return db.execute(
        select(func.count())
        .select_from(UserRole)
        .join(User, User.id == UserRole.user_id)
        .where(UserRole.role_id == admin_role_id, User.is_active.is_(True))
    ).scalar_one()


def has_role(db: Session, user_id: int, role_id: int) -> bool:
    return db.get(UserRole, (user_id, role_id)) is not None


def create_user(
    db: Session,
    *,
    email: str,
    display_name: str,
    password: str,
    must_change_password: bool,
    now: datetime,
) -> User:
    user = User(
        email=email,
        display_name=display_name,
        password_hash=passwords.hash_password(password),
        must_change_password=must_change_password,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()
    return user


def set_temporary_password(db: Session, user: User, password: str, now: datetime) -> None:
    """New temporary password: forced change at next login, sessions revoked, lock cleared."""
    user.password_hash = passwords.hash_password(password)
    user.must_change_password = True
    user.updated_at = now
    revoke_user_sessions(db, user.id, now)
    lockout.clear(db, user.email, now)


def assign_role_audited(
    db: Session,
    audit: AuditService,
    *,
    user: User,
    role: Role,
    actor: Actor,
    now: datetime,
    request_id: str | None,
) -> None:
    if has_role(db, user.id, role.id):
        return
    db.add(UserRole(user_id=user.id, role_id=role.id))
    db.flush()
    audit.record(
        db,
        AuditEventIn(
            action=actions.ROLE_ASSIGN,
            outcome="success",
            actor=actor,
            occurred_at=now,
            target_type="user",
            target_id=str(user.id),
            request_id=request_id,
            metadata={"role_id": role.id, "role_name": role.name},
        ),
    )
