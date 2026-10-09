"""The single authorization policy layer (FR-A8, BR-A1..BR-A4).

Every authorization decision is computed here from current database state, per request.
Client-supplied roles, permissions or user ids are never consulted. Future data policies
(row-level security, table grants) attach to roles through this service, next to the
export-column grants, without changing endpoint code.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from cohortsplit.auth.catalog import ALL_PERMISSIONS
from cohortsplit.auth.models import Role, RolePermission, User, UserRole


@dataclass(frozen=True)
class RoleRef:
    id: int
    name: str


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of the current request."""

    user_id: int
    email: str
    display_name: str
    session_id: int
    is_admin: bool
    roles: tuple[RoleRef, ...]
    permissions: frozenset[str]

    def has(self, permission: str) -> bool:
        return permission in self.permissions


class PolicyService:
    def roles_of(self, db: Session, user_id: int) -> list[Role]:
        return list(
            db.execute(
                select(Role)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user_id)
                .order_by(Role.name)
            ).scalars()
        )

    def permissions_for_roles(self, db: Session, roles: list[Role]) -> frozenset[str]:
        """Union over roles (BR-A2). The Admin system role holds the whole catalog."""
        if any(role.is_system for role in roles):
            return ALL_PERMISSIONS
        if not roles:
            return frozenset()
        keys = db.execute(
            select(RolePermission.permission_key).where(
                RolePermission.role_id.in_([role.id for role in roles])
            )
        ).scalars()
        # Intersect with the code catalog: a stale row never grants an unknown permission.
        return frozenset(keys) & ALL_PERMISSIONS

    def effective_permissions(self, db: Session, user_id: int) -> frozenset[str]:
        return self.permissions_for_roles(db, self.roles_of(db, user_id))

    def principal_for(self, db: Session, user: User, session_id: int) -> Principal:
        roles = self.roles_of(db, user.id)
        return Principal(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            session_id=session_id,
            is_admin=any(role.is_system for role in roles),
            roles=tuple(RoleRef(role.id, role.name) for role in roles),
            permissions=self.permissions_for_roles(db, roles),
        )
