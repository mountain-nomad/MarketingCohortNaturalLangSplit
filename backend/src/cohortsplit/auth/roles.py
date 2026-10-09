"""Custom role management: permissions and export-column grants (FR-A3, FR-A4).

The Admin system role is protected: it cannot be renamed, edited, deleted, or have its
membership changed from here (it is assigned from the Users screen or the CLI).
"""

import re
from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from cohortsplit.audit import actions
from cohortsplit.auth.catalog import ADMIN_ONLY_PERMISSIONS, ALL_PERMISSIONS
from cohortsplit.auth.changes import ChangeContext
from cohortsplit.auth.errors import (
    ApiError,
    ConflictError,
    NotFoundError,
    UnprocessableError,
)
from cohortsplit.auth.models import Role, RoleExportColumn, RolePermission, User, UserRole

MAX_ROLE_NAME_LENGTH = 100
_IDENT = r"[A-Za-z_][A-Za-z0-9_$]{0,62}"
COLUMN_REF_RE = re.compile(rf"^{_IDENT}\.{_IDENT}\.{_IDENT}$")


class SystemRoleProtectedError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            403,
            "system_role_protected",
            "The Admin role is protected: it cannot be edited or deleted, and it is "
            "assigned from the Users screen.",
        )


def get_role(db: Session, role_id: int) -> Role:
    role = db.get(Role, role_id)
    if role is None:
        raise NotFoundError("Role")
    return role


def validate_name(name: str) -> str:
    cleaned = name.strip()
    if not 1 <= len(cleaned) <= MAX_ROLE_NAME_LENGTH:
        raise UnprocessableError(
            "invalid_role_name", f"Role name must be 1 to {MAX_ROLE_NAME_LENGTH} characters."
        )
    return cleaned


def validate_permissions(keys: Iterable[str]) -> list[str]:
    requested = sorted(set(keys))
    unknown = [k for k in requested if k not in ALL_PERMISSIONS]
    if unknown:
        raise UnprocessableError(
            "unknown_permission", "Unknown permission(s).", permissions=unknown
        )
    admin_only = [k for k in requested if k in ADMIN_ONLY_PERMISSIONS]
    if admin_only:
        raise UnprocessableError(
            "permission_not_grantable",
            "User and role management permissions belong to the Admin role only.",
            permissions=admin_only,
        )
    return requested


def validate_columns(columns: Iterable[str]) -> list[str]:
    requested = sorted(set(columns))
    invalid = [c for c in requested if not COLUMN_REF_RE.fullmatch(c)]
    if invalid:
        raise UnprocessableError(
            "invalid_column",
            "Export columns must look like schema.table.column.",
            columns=invalid,
        )
    return requested


def _ensure_name_free(db: Session, name: str, exclude_role_id: int | None = None) -> None:
    statement = select(Role.id).where(func.lower(Role.name) == name.lower())
    if exclude_role_id is not None:
        statement = statement.where(Role.id != exclude_role_id)
    if db.execute(statement).first() is not None:
        raise ConflictError("role_name_taken", "A role with this name already exists.")


def permissions_of(db: Session, role: Role) -> list[str]:
    if role.is_system:
        return sorted(ALL_PERMISSIONS)
    return sorted(
        db.execute(
            select(RolePermission.permission_key).where(RolePermission.role_id == role.id)
        ).scalars()
    )


def export_columns_of(db: Session, role: Role) -> list[str]:
    return sorted(
        db.execute(
            select(RoleExportColumn.column_ref).where(RoleExportColumn.role_id == role.id)
        ).scalars()
    )


def member_ids_of(db: Session, role: Role) -> list[int]:
    return sorted(db.execute(select(UserRole.user_id).where(UserRole.role_id == role.id)).scalars())


def _replace_permissions(db: Session, role: Role, keys: list[str]) -> None:
    db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    db.add_all(RolePermission(role_id=role.id, permission_key=k) for k in keys)


def _replace_columns(db: Session, role: Role, columns: list[str]) -> None:
    db.execute(delete(RoleExportColumn).where(RoleExportColumn.role_id == role.id))
    db.add_all(RoleExportColumn(role_id=role.id, column_ref=c) for c in columns)


def create_role(
    db: Session,
    ctx: ChangeContext,
    *,
    name: str,
    description: str,
    permissions: Iterable[str],
    export_columns: Iterable[str],
) -> Role:
    cleaned = validate_name(name)
    keys = validate_permissions(permissions)
    columns = validate_columns(export_columns)
    _ensure_name_free(db, cleaned)
    role = Role(
        name=cleaned,
        description=description.strip(),
        is_system=False,
        created_at=ctx.now,
        updated_at=ctx.now,
    )
    db.add(role)
    db.flush()
    _replace_permissions(db, role, keys)
    _replace_columns(db, role, columns)
    db.flush()
    ctx.record(
        db,
        actions.ROLE_CREATE,
        target_type="role",
        target_id=role.id,
        metadata={
            "name": role.name,
            "permissions": keys,
            "export_columns": columns,
        },
    )
    return role


def update_role(
    db: Session,
    ctx: ChangeContext,
    role: Role,
    *,
    name: str | None,
    description: str | None,
    permissions: Iterable[str] | None,
    export_columns: Iterable[str] | None,
) -> Role:
    if role.is_system:
        raise SystemRoleProtectedError()
    changes: dict[str, object] = {}

    if name is not None:
        cleaned = validate_name(name)
        if cleaned != role.name:
            _ensure_name_free(db, cleaned, exclude_role_id=role.id)
            changes["name"] = {"before": role.name, "after": cleaned}
            role.name = cleaned
    if description is not None and description.strip() != role.description:
        changes["description"] = {"before": role.description, "after": description.strip()}
        role.description = description.strip()
    if permissions is not None:
        keys = validate_permissions(permissions)
        before = permissions_of(db, role)
        if keys != before:
            _replace_permissions(db, role, keys)
            changes["permissions"] = {"before": before, "after": keys}
    if export_columns is not None:
        columns = validate_columns(export_columns)
        before_columns = export_columns_of(db, role)
        if columns != before_columns:
            _replace_columns(db, role, columns)
            changes["export_columns"] = {"before": before_columns, "after": columns}

    if changes:
        role.updated_at = ctx.now
        db.flush()
        ctx.record(
            db,
            actions.ROLE_UPDATE,
            target_type="role",
            target_id=role.id,
            metadata={"role_name": role.name, **changes},
        )
    return role


def delete_role(db: Session, ctx: ChangeContext, role: Role, *, confirm: bool) -> None:
    if role.is_system:
        raise SystemRoleProtectedError()
    members = member_ids_of(db, role)
    if members and not confirm:
        raise ConflictError(
            "confirmation_required",
            f"This role is assigned to {len(members)} user(s). Deleting it removes its "
            "permissions and export grants from them immediately. Confirm to continue.",
            member_count=len(members),
        )
    ctx.record(
        db,
        actions.ROLE_DELETE,
        target_type="role",
        target_id=role.id,
        metadata={
            "name": role.name,
            "permissions": permissions_of(db, role),
            "export_columns": export_columns_of(db, role),
            "member_ids": members,
        },
    )
    db.execute(delete(UserRole).where(UserRole.role_id == role.id))
    db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    db.execute(delete(RoleExportColumn).where(RoleExportColumn.role_id == role.id))
    db.execute(delete(Role).where(Role.id == role.id))
    db.expunge(role)


def set_members(db: Session, ctx: ChangeContext, role: Role, user_ids: Iterable[int]) -> None:
    if role.is_system:
        raise SystemRoleProtectedError()
    wanted = set(user_ids)
    found = (
        set(db.execute(select(User.id).where(User.id.in_(wanted))).scalars()) if wanted else set()
    )
    missing = sorted(wanted - found)
    if missing:
        raise UnprocessableError("unknown_user", "Unknown user id(s).", user_ids=missing)
    current = set(member_ids_of(db, role))
    for user_id in sorted(wanted - current):
        db.add(UserRole(user_id=user_id, role_id=role.id))
        db.flush()
        ctx.record(
            db,
            actions.ROLE_ASSIGN,
            target_type="user",
            target_id=user_id,
            metadata={"role_id": role.id, "role_name": role.name},
        )
    for user_id in sorted(current - wanted):
        db.execute(delete(UserRole).where(UserRole.role_id == role.id, UserRole.user_id == user_id))
        ctx.record(
            db,
            actions.ROLE_UNASSIGN,
            target_type="user",
            target_id=user_id,
            metadata={"role_id": role.id, "role_name": role.name},
        )
