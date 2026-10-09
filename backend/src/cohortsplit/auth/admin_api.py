"""Admin endpoints: users, roles, permission catalog. Each endpoint names its permission."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from cohortsplit.audit.service import Actor, AuditService
from cohortsplit.auth import roles as role_service
from cohortsplit.auth import user_admin
from cohortsplit.auth.catalog import PERMISSION_CATALOG
from cohortsplit.auth.changes import ChangeContext
from cohortsplit.auth.clock import Clock, get_clock
from cohortsplit.auth.crawler_integration import ColumnInventory, get_column_inventory
from cohortsplit.auth.dependencies import (
    DbSession,
    ensure_permission,
    get_audit,
    get_request_id,
    require_permission,
)
from cohortsplit.auth.models import Role, User
from cohortsplit.auth.policy import Principal

router = APIRouter(prefix="/api/admin", tags=["admin"])


UserRead = Annotated[Principal, Depends(require_permission("user.read"))]
UserCreate = Annotated[Principal, Depends(require_permission("user.create"))]
UserUpdate = Annotated[Principal, Depends(require_permission("user.update"))]
UserDeactivate = Annotated[Principal, Depends(require_permission("user.deactivate"))]
UserResetPassword = Annotated[Principal, Depends(require_permission("user.reset_password"))]
RoleRead = Annotated[Principal, Depends(require_permission("role.read"))]
RoleCreate = Annotated[Principal, Depends(require_permission("role.create"))]
RoleUpdate = Annotated[Principal, Depends(require_permission("role.update"))]
RoleDelete = Annotated[Principal, Depends(require_permission("role.delete"))]
RoleAssign = Annotated[Principal, Depends(require_permission("role.assign"))]


def _ctx(
    request: Request, principal: Principal, audit: AuditService, clock: Clock
) -> ChangeContext:
    return ChangeContext(
        audit=audit,
        actor=Actor.user(principal.user_id),
        now=clock(),
        request_id=get_request_id(request),
    )


Audit = Annotated[AuditService, Depends(get_audit)]
Now = Annotated[Clock, Depends(get_clock)]
Inventory = Annotated[ColumnInventory, Depends(get_column_inventory)]


# --- schemas ------------------------------------------------------------------------------


class RoleRefOut(BaseModel):
    id: int
    name: str


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    is_active: bool
    must_change_password: bool
    roles: list[RoleRefOut]
    created_at: datetime
    last_login_at: datetime | None


class UserListOut(BaseModel):
    items: list[UserOut]


class UserCreateIn(BaseModel):
    email: str = Field(max_length=320)
    display_name: str = Field(max_length=1000)
    temporary_password: SecretStr
    role_ids: list[int] = []


class UserUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=1000)


class UserRolesIn(BaseModel):
    role_ids: list[int]


class ResetPasswordIn(BaseModel):
    temporary_password: SecretStr


class PermissionOut(BaseModel):
    key: str
    area: str
    description: str
    grantable: bool


class PermissionListOut(BaseModel):
    items: list[PermissionOut]


class RoleOut(BaseModel):
    id: int
    name: str
    description: str
    is_system: bool
    permissions: list[str]
    export_columns: list[str]
    # Grants on columns absent from the latest crawl (inert); empty when no crawl exists.
    missing_export_columns: list[str]
    column_inventory: Literal["available", "unavailable"]
    member_ids: list[int]


class RoleListOut(BaseModel):
    items: list[RoleOut]


class RoleCreateIn(BaseModel):
    name: str = Field(max_length=1000)
    description: str = Field(default="", max_length=2000)
    permissions: list[str] = []
    export_columns: list[str] = []


class RoleUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=1000)
    description: str | None = Field(default=None, max_length=2000)
    permissions: list[str] | None = None
    export_columns: list[str] | None = None


class RoleMembersIn(BaseModel):
    user_ids: list[int]


def user_out(db: Session, user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        roles=[RoleRefOut(id=r.id, name=r.name) for r in user_admin.roles_of(db, user.id)],
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def role_out(db: Session, role: Role, crawled: frozenset[str] | None) -> RoleOut:
    columns = role_service.export_columns_of(db, role)
    return RoleOut(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=role_service.permissions_of(db, role),
        export_columns=columns,
        missing_export_columns=[] if crawled is None else [c for c in columns if c not in crawled],
        column_inventory="unavailable" if crawled is None else "available",
        member_ids=role_service.member_ids_of(db, role),
    )


# --- users --------------------------------------------------------------------------------


@router.get("/users", response_model=UserListOut)
def list_users(_: UserRead, db: DbSession) -> UserListOut:
    return UserListOut(items=[user_out(db, u) for u in user_admin.list_users(db)])


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreateIn,
    request: Request,
    principal: UserCreate,
    db: DbSession,
    audit: Audit,
    clock: Now,
) -> UserOut:
    if body.role_ids:
        ensure_permission(request, principal, "role.assign", audit, clock)
    user = user_admin.create(
        db,
        _ctx(request, principal, audit, clock),
        email=body.email,
        display_name=body.display_name,
        temporary_password=body.temporary_password.get_secret_value(),
        role_ids=body.role_ids,
    )
    db.commit()
    return user_out(db, user)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, _: UserRead, db: DbSession) -> UserOut:
    return user_out(db, user_admin.get_user(db, user_id))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdateIn,
    request: Request,
    principal: UserUpdate,
    db: DbSession,
    audit: Audit,
    clock: Now,
) -> UserOut:
    user = user_admin.get_user(db, user_id)
    user_admin.update(
        db, _ctx(request, principal, audit, clock), user, display_name=body.display_name
    )
    db.commit()
    return user_out(db, user)


@router.put("/users/{user_id}/roles", response_model=UserOut)
def set_user_roles(
    user_id: int,
    body: UserRolesIn,
    request: Request,
    principal: RoleAssign,
    db: DbSession,
    audit: Audit,
    clock: Now,
) -> UserOut:
    user = user_admin.get_user(db, user_id)
    user_admin.set_roles(db, _ctx(request, principal, audit, clock), user, body.role_ids)
    db.commit()
    return user_out(db, user)


@router.post("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(
    user_id: int,
    request: Request,
    principal: UserDeactivate,
    db: DbSession,
    audit: Audit,
    clock: Now,
) -> UserOut:
    user = user_admin.get_user(db, user_id)
    user_admin.deactivate(db, _ctx(request, principal, audit, clock), user)
    db.commit()
    return user_out(db, user)


@router.post("/users/{user_id}/reactivate", response_model=UserOut)
def reactivate_user(
    user_id: int,
    request: Request,
    principal: UserDeactivate,
    db: DbSession,
    audit: Audit,
    clock: Now,
) -> UserOut:
    user = user_admin.get_user(db, user_id)
    user_admin.reactivate(db, _ctx(request, principal, audit, clock), user)
    db.commit()
    return user_out(db, user)


@router.post("/users/{user_id}/reset-password", response_model=UserOut)
def reset_user_password(
    user_id: int,
    body: ResetPasswordIn,
    request: Request,
    principal: UserResetPassword,
    db: DbSession,
    audit: Audit,
    clock: Now,
) -> UserOut:
    user = user_admin.get_user(db, user_id)
    user_admin.reset_password(
        db,
        _ctx(request, principal, audit, clock),
        user,
        temporary_password=body.temporary_password.get_secret_value(),
    )
    db.commit()
    return user_out(db, user)


# --- permissions and roles ----------------------------------------------------------------


@router.get("/permissions", response_model=PermissionListOut)
def list_permissions(_: RoleRead) -> PermissionListOut:
    return PermissionListOut(
        items=[
            PermissionOut(key=p.key, area=p.area, description=p.description, grantable=p.grantable)
            for p in PERMISSION_CATALOG
        ]
    )


@router.get("/roles", response_model=RoleListOut)
def list_roles(_: RoleRead, db: DbSession, inventory: Inventory) -> RoleListOut:
    roles = db.query(Role).order_by(Role.is_system.desc(), Role.name).all()
    crawled = inventory.columns()
    return RoleListOut(items=[role_out(db, r, crawled) for r in roles])


@router.post("/roles", response_model=RoleOut, status_code=201)
def create_role(
    body: RoleCreateIn,
    request: Request,
    principal: RoleCreate,
    db: DbSession,
    audit: Audit,
    clock: Now,
    inventory: Inventory,
) -> RoleOut:
    role = role_service.create_role(
        db,
        _ctx(request, principal, audit, clock),
        name=body.name,
        description=body.description,
        permissions=body.permissions,
        export_columns=body.export_columns,
    )
    db.commit()
    return role_out(db, role, inventory.columns())


@router.get("/roles/{role_id}", response_model=RoleOut)
def get_role(role_id: int, _: RoleRead, db: DbSession, inventory: Inventory) -> RoleOut:
    return role_out(db, role_service.get_role(db, role_id), inventory.columns())


@router.patch("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: int,
    body: RoleUpdateIn,
    request: Request,
    principal: RoleUpdate,
    db: DbSession,
    audit: Audit,
    clock: Now,
    inventory: Inventory,
) -> RoleOut:
    role = role_service.get_role(db, role_id)
    role_service.update_role(
        db,
        _ctx(request, principal, audit, clock),
        role,
        name=body.name,
        description=body.description,
        permissions=body.permissions,
        export_columns=body.export_columns,
    )
    db.commit()
    return role_out(db, role, inventory.columns())


@router.delete("/roles/{role_id}", status_code=204, response_class=Response)
def delete_role(
    role_id: int,
    request: Request,
    principal: RoleDelete,
    db: DbSession,
    audit: Audit,
    clock: Now,
    confirm: bool = False,
) -> Response:
    role = role_service.get_role(db, role_id)
    role_service.delete_role(db, _ctx(request, principal, audit, clock), role, confirm=confirm)
    db.commit()
    return Response(status_code=204)


@router.put("/roles/{role_id}/members", response_model=RoleOut)
def set_role_members(
    role_id: int,
    body: RoleMembersIn,
    request: Request,
    principal: RoleAssign,
    db: DbSession,
    audit: Audit,
    clock: Now,
    inventory: Inventory,
) -> RoleOut:
    role = role_service.get_role(db, role_id)
    role_service.set_members(db, _ctx(request, principal, audit, clock), role, body.user_ids)
    db.commit()
    return role_out(db, role, inventory.columns())
