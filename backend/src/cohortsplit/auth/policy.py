"""The single authorization policy layer (FR-A8, BR-A1..BR-A4).

Every authorization decision is computed here from current database state, per request.
Client-supplied roles, permissions or user ids are never consulted. Future data policies
(row-level security, table grants) attach to roles through this service, next to the
export-column grants, without changing endpoint code.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cohortsplit.audit import actions
from cohortsplit.audit.service import Actor, AuditEventIn, AuditService, AuditWriteError
from cohortsplit.auth.catalog import ALL_PERMISSIONS
from cohortsplit.auth.errors import ApiError, PermissionDeniedError
from cohortsplit.auth.models import Role, RoleExportColumn, RolePermission, User, UserRole

logger = logging.getLogger(__name__)

EXPORT_PERMISSION = "cohort.export"


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


@dataclass(frozen=True)
class ExportColumnPolicy:
    """Which columns the principal may export (BR-A3)."""

    canonical_user_id: bool
    granted_columns: frozenset[str]

    def allows(self, column: str) -> bool:
        return self.canonical_user_id and column in self.granted_columns


class ExportColumnDeniedError(ApiError):
    def __init__(self, columns: Sequence[str]) -> None:
        super().__init__(
            403,
            "export_column_denied",
            "None of your roles is granted export of: " + ", ".join(columns) + ".",
            extra={"columns": list(columns)},
        )


class PolicyService:
    def __init__(self, audit: AuditService) -> None:
        self._audit = audit

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

    def effective_export_columns(self, db: Session, principal: Principal) -> ExportColumnPolicy:
        """Canonical user id with ``cohort.export``, plus grants of all the user's roles (BR-A3).

        Grants are role-attached data policies: without ``cohort.export`` they confer
        nothing. The Admin role holds every permission but no implicit column grants.
        """
        if not principal.has(EXPORT_PERMISSION):
            return ExportColumnPolicy(canonical_user_id=False, granted_columns=frozenset())
        role_ids = [role.id for role in principal.roles]
        granted: frozenset[str] = frozenset()
        if role_ids:
            granted = frozenset(
                db.execute(
                    select(RoleExportColumn.column_ref).where(
                        RoleExportColumn.role_id.in_(role_ids)
                    )
                ).scalars()
            )
        return ExportColumnPolicy(canonical_user_id=True, granted_columns=granted)

    def authorize_export(
        self,
        db: Session,
        principal: Principal,
        *,
        columns: Sequence[str],
        run_id: str | None,
        redownload: bool,
        row_counts: Mapping[str, int] | None,
        request_id: str | None,
        now: datetime,
    ) -> ExportColumnPolicy:
        """The export gate. Call immediately before producing export data.

        ``columns`` are the extra columns (``schema.table.column``) beyond the canonical
        user id. Raises PermissionDeniedError / ExportColumnDeniedError (denial audited)
        or AuditWriteError when the success event cannot be recorded (fail closed).
        """
        requested = sorted(set(columns))
        action = actions.COHORT_REDOWNLOAD if redownload else actions.COHORT_EXPORT

        def event(outcome: str, **metadata: object) -> AuditEventIn:
            return AuditEventIn(
                action=action,
                outcome="success" if outcome == "success" else "denied",
                actor=Actor.user(principal.user_id),
                occurred_at=now,
                target_type="cohort_run",
                target_id=run_id,
                request_id=request_id,
                metadata={"columns": requested, **metadata},
            )

        def record_denial(denial: AuditEventIn) -> None:
            try:
                self._audit.record_detached(denial, sensitive=True)
            except AuditWriteError:
                logger.error("export denial could not be audited; refusing anyway")

        policy = self.effective_export_columns(db, principal)
        if not policy.canonical_user_id:
            record_denial(event("denied", reason="missing_permission"))
            raise PermissionDeniedError()
        denied = [column for column in requested if not policy.allows(column)]
        if denied:
            record_denial(event("denied", reason="column_not_granted", denied_columns=denied))
            raise ExportColumnDeniedError(denied)
        self._audit.record_detached(
            event("success", row_counts=dict(row_counts or {})), sensitive=True
        )
        return policy

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
