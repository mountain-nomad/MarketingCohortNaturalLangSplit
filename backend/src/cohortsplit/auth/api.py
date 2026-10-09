"""Authentication endpoints: login, logout, password change, current user."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, SecretStr

from cohortsplit.auth.clock import Clock, get_clock
from cohortsplit.auth.dependencies import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    CurrentPrincipal,
    CurrentSession,
    DbSession,
    get_auth_service,
    get_request_id,
    get_settings,
)
from cohortsplit.auth.service import AuthService
from cohortsplit.config import Settings

router = APIRouter(prefix="/api", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: SecretStr


class UserSummary(BaseModel):
    id: int
    email: str
    display_name: str


class LoginOut(BaseModel):
    user: UserSummary
    must_change_password: bool
    csrf_token: str


class PasswordChangeIn(BaseModel):
    current_password: SecretStr
    new_password: SecretStr


class RoleRefOut(BaseModel):
    id: int
    name: str


class MeOut(BaseModel):
    id: int
    email: str
    display_name: str
    is_admin: bool
    roles: list[RoleRefOut]
    permissions: list[str]


def _cookie_secure(request: Request, settings: Settings) -> bool:
    if settings.cookie_secure is not None:
        return settings.cookie_secure
    return request.url.scheme == "https"


def _clear_cookies(response: Response, secure: bool) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/api", secure=secure, httponly=True, samesite="strict"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, samesite="strict")


@router.post("/auth/login", response_model=LoginOut)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: DbSession,
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> LoginOut:
    result = service.login(
        db,
        email=body.email,
        password=body.password.get_secret_value(),
        now=clock(),
        request_id=get_request_id(request),
    )
    secure = _cookie_secure(request, settings)
    max_age = settings.session_max_lifetime_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        result.session.token,
        max_age=max_age,
        path="/api",
        secure=secure,
        httponly=True,
        samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE,
        result.session.csrf_token,
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=False,
        samesite="strict",
    )
    return LoginOut(
        user=UserSummary(
            id=result.user.id, email=result.user.email, display_name=result.user.display_name
        ),
        must_change_password=result.user.must_change_password,
        csrf_token=result.session.csrf_token,
    )


@router.post("/auth/logout", status_code=204, response_class=Response)
def logout(
    request: Request,
    context: CurrentSession,
    db: DbSession,
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> Response:
    service.logout(
        db,
        user_id=context.user.id,
        session_id=context.session.id,
        now=clock(),
        request_id=get_request_id(request),
    )
    response = Response(status_code=204)
    _clear_cookies(response, _cookie_secure(request, settings))
    return response


@router.post("/auth/password", status_code=204, response_class=Response)
def change_password(
    body: PasswordChangeIn,
    request: Request,
    context: CurrentSession,
    db: DbSession,
    service: Annotated[AuthService, Depends(get_auth_service)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> Response:
    service.change_password(
        db,
        user=context.user,
        session_id=context.session.id,
        current_password=body.current_password.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
        now=clock(),
        request_id=get_request_id(request),
    )
    return Response(status_code=204)


@router.get("/me", response_model=MeOut)
def me(principal: CurrentPrincipal) -> MeOut:
    return MeOut(
        id=principal.user_id,
        email=principal.email,
        display_name=principal.display_name,
        is_admin=principal.is_admin,
        roles=[RoleRefOut(id=r.id, name=r.name) for r in principal.roles],
        permissions=sorted(principal.permissions),
    )
