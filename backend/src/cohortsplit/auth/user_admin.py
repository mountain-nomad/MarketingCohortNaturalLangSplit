"""Admin user management (FR-A2) with the last-active-admin invariant (BR-A5).

Admin-membership changes lock the Admin role row (``SELECT ... FOR UPDATE``) before
counting active admins, so two concurrent demotions cannot leave zero admins.
"""

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cohortsplit.audit import actions
from cohortsplit.auth import passwords
from cohortsplit.auth.changes import ChangeContext
from cohortsplit.auth.errors import ConflictError, NotFoundError, UnprocessableError
from cohortsplit.auth.models import Role, User, UserRole
from cohortsplit.auth.sessions import revoke_user_sessions
from cohortsplit.auth.users import (
    InvalidEmailError,
    admin_role,
    count_active_admins,
    create_user,
    find_by_email,
    has_role,
    set_temporary_password,
    validate_email,
)

MAX_DISPLAY_NAME_LENGTH = 200


def get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User")
    return user


def list_users(db: Session) -> list[User]:
    return list(db.execute(select(User).order_by(User.email)).scalars())


def roles_of(db: Session, user_id: int) -> list[Role]:
    return list(
        db.execute(
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.name)
        ).scalars()
    )


def _display_name(value: str) -> str:
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= MAX_DISPLAY_NAME_LENGTH:
        raise UnprocessableError(
            "invalid_display_name",
            f"Display name must be 1 to {MAX_DISPLAY_NAME_LENGTH} characters.",
        )
    return cleaned


def _temporary_password(value: str) -> str:
    try:
        passwords.validate_password_policy(value)
    except passwords.PasswordPolicyError as exc:
        raise UnprocessableError("password_policy", str(exc)) from None
    return value


def _roles_by_id(db: Session, role_ids: Iterable[int]) -> list[Role]:
    wanted = set(role_ids)
    if not wanted:
        return []
    roles = list(db.execute(select(Role).where(Role.id.in_(wanted))).scalars())
    missing = sorted(wanted - {r.id for r in roles})
    if missing:
        raise UnprocessableError("unknown_role", "Unknown role id(s).", role_ids=missing)
    return roles


def _assign(db: Session, ctx: ChangeContext, user: User, role: Role) -> None:
    db.add(UserRole(user_id=user.id, role_id=role.id))
    db.flush()
    ctx.record(
        db,
        actions.ROLE_ASSIGN,
        target_type="user",
        target_id=user.id,
        metadata={"role_id": role.id, "role_name": role.name},
    )


def _unassign(db: Session, ctx: ChangeContext, user: User, role: Role) -> None:
    db.execute(delete(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id))
    ctx.record(
        db,
        actions.ROLE_UNASSIGN,
        target_type="user",
        target_id=user.id,
        metadata={"role_id": role.id, "role_name": role.name},
    )


def _refuse_if_last_active_admin(db: Session, user: User) -> None:
    """Call before removing ``user`` from the active admins (lock held until commit)."""
    admin = admin_role(db, for_update=True)
    if (
        user.is_active
        and has_role(db, user.id, admin.id)
        and count_active_admins(db, admin.id) <= 1
    ):
        raise ConflictError(
            "last_admin",
            "At least one active admin must remain. Make another user an admin first.",
        )


def create(
    db: Session,
    ctx: ChangeContext,
    *,
    email: str,
    display_name: str,
    temporary_password: str,
    role_ids: Iterable[int],
) -> User:
    try:
        normalized = validate_email(email)
    except InvalidEmailError as exc:
        raise UnprocessableError("invalid_email", str(exc)) from None
    name = _display_name(display_name)
    password = _temporary_password(temporary_password)
    roles = _roles_by_id(db, role_ids)
    if find_by_email(db, normalized) is not None:
        raise ConflictError("email_taken", "A user with this email already exists.")
    try:
        user = create_user(
            db,
            email=normalized,
            display_name=name,
            password=password,
            must_change_password=True,
            now=ctx.now,
        )
    except IntegrityError:  # concurrent create with the same email
        db.rollback()
        raise ConflictError("email_taken", "A user with this email already exists.") from None
    ctx.record(db, actions.USER_CREATE, target_type="user", target_id=user.id)
    for role in sorted(roles, key=lambda r: r.name):
        _assign(db, ctx, user, role)
    return user


def update(db: Session, ctx: ChangeContext, user: User, *, display_name: str | None) -> User:
    if display_name is not None:
        name = _display_name(display_name)
        if name != user.display_name:
            before = user.display_name
            user.display_name = name
            user.updated_at = ctx.now
            ctx.record(
                db,
                actions.USER_UPDATE,
                target_type="user",
                target_id=user.id,
                metadata={"display_name": {"before": before, "after": name}},
            )
    return user


def set_roles(db: Session, ctx: ChangeContext, user: User, role_ids: Iterable[int]) -> User:
    wanted = {r.id: r for r in _roles_by_id(db, role_ids)}
    current = {r.id: r for r in roles_of(db, user.id)}
    removed = [current[i] for i in sorted(set(current) - set(wanted))]
    added = [wanted[i] for i in sorted(set(wanted) - set(current))]
    if any(role.is_system for role in removed):
        _refuse_if_last_active_admin(db, user)
    for role in added:
        _assign(db, ctx, user, role)
    for role in removed:
        _unassign(db, ctx, user, role)
    return user


def deactivate(db: Session, ctx: ChangeContext, user: User) -> User:
    if not user.is_active:
        return user
    _refuse_if_last_active_admin(db, user)
    user.is_active = False
    user.updated_at = ctx.now
    revoke_user_sessions(db, user.id, ctx.now)
    ctx.record(db, actions.USER_DEACTIVATE, target_type="user", target_id=user.id)
    return user


def reactivate(db: Session, ctx: ChangeContext, user: User) -> User:
    if user.is_active:
        return user
    user.is_active = True
    user.updated_at = ctx.now
    ctx.record(db, actions.USER_REACTIVATE, target_type="user", target_id=user.id)
    return user


def reset_password(db: Session, ctx: ChangeContext, user: User, *, temporary_password: str) -> User:
    password = _temporary_password(temporary_password)
    set_temporary_password(db, user, password, ctx.now)
    ctx.record(db, actions.USER_PASSWORD_RESET, target_type="user", target_id=user.id)
    return user
