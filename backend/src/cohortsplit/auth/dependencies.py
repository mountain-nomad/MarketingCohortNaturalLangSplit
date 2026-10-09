"""FastAPI dependencies: the reusable entry points of the policy layer.

Later features use:

* ``CurrentPrincipal`` / ``current_principal`` — the authenticated caller (401 without a
  live session, 403 ``password_change_required`` while a temporary password is set,
  403 ``csrf_failed`` on unsafe methods without a valid ``X-CSRF-Token``);
* ``require_permission("cohort.export")`` — 403 + audited denial when missing;
* ``get_db``, ``get_audit``, ``get_policy``, ``get_request_id``, ``get_clock``.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.audit import actions
from cohortsplit.audit.service import Actor, AuditEventIn, AuditService
from cohortsplit.auth.catalog import ALL_PERMISSIONS
from cohortsplit.auth.clock import Clock, get_clock
from cohortsplit.auth.crawler_integration import ColumnInventory
from cohortsplit.auth.errors import (
    CsrfError,
    NotAuthenticatedError,
    PasswordChangeRequiredError,
    PermissionDeniedError,
)
from cohortsplit.auth.models import AuthSession, User
from cohortsplit.auth.policy import PolicyService, Principal
from cohortsplit.auth.service import AuthService
from cohortsplit.auth.sessions import csrf_matches, resolve_session
from cohortsplit.config import Settings

SESSION_COOKIE = "cohortsplit_session"
CSRF_COOKIE = "cohortsplit_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class AuthState:
    """Per-application auth objects, stored on ``app.state.auth``."""

    settings: Settings
    session_factory: sessionmaker[Session]
    audit: AuditService
    policy: PolicyService
    auth: AuthService
    # Crawled-column inventory for "missing" grant marks (cohortsplit.auth.crawler_integration).
    column_inventory: ColumnInventory | None = None


def _state(request: Request) -> AuthState:
    state: AuthState = request.app.state.auth
    return state


def get_settings(request: Request) -> Settings:
    return _state(request).settings


def get_db(request: Request) -> Iterator[Session]:
    with _state(request).session_factory() as db:
        yield db


def get_audit(request: Request) -> AuditService:
    return _state(request).audit


def get_policy(request: Request) -> PolicyService:
    return _state(request).policy


def get_auth_service(request: Request) -> AuthService:
    return _state(request).auth


def get_request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


DbSession = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class SessionContext:
    session: AuthSession
    user: User


def current_session(
    request: Request,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> SessionContext:
    """A live session of an active user; allowed even while a password change is pending."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise NotAuthenticatedError()
    resolved = resolve_session(db, token, clock(), settings)
    if resolved is None:
        raise NotAuthenticatedError()
    session, user = resolved
    if request.method not in SAFE_METHODS and not csrf_matches(
        session, request.headers.get(CSRF_HEADER)
    ):
        raise CsrfError()
    return SessionContext(session, user)


CurrentSession = Annotated[SessionContext, Depends(current_session)]


def current_principal(
    context: CurrentSession,
    db: DbSession,
    policy: Annotated[PolicyService, Depends(get_policy)],
) -> Principal:
    """The authenticated caller with permissions evaluated from current DB state."""
    if context.user.must_change_password:
        raise PasswordChangeRequiredError()
    return policy.principal_for(db, context.user, context.session.id)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def deny(
    request: Request,
    principal: Principal,
    permission: str,
    audit: AuditService,
    clock: Clock,
) -> PermissionDeniedError:
    """Audit a denied permission check (best effort) and return the error to raise."""
    audit.record_detached(
        AuditEventIn(
            action=actions.ACCESS_DENIED,
            outcome="denied",
            actor=Actor.user(principal.user_id),
            occurred_at=clock(),
            target_type="endpoint",
            target_id=f"{request.method} {request.url.path}",
            request_id=get_request_id(request),
            metadata={"permission": permission},
        ),
        sensitive=False,
    )
    return PermissionDeniedError()


def require_permission(permission: str) -> Callable[..., Principal]:
    """Dependency factory: the caller must hold ``permission`` (checked per request)."""
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")

    def dependency(
        request: Request,
        principal: CurrentPrincipal,
        audit: Annotated[AuditService, Depends(get_audit)],
        clock: Annotated[Clock, Depends(get_clock)],
    ) -> Principal:
        if principal.has(permission):
            return principal
        raise deny(request, principal, permission, audit, clock)

    dependency.__name__ = f"require_{permission.replace('.', '_')}"
    return dependency


def ensure_permission(
    request: Request,
    principal: Principal,
    permission: str,
    audit: AuditService,
    clock: Clock,
) -> None:
    """Imperative variant for permissions that depend on the request body."""
    if not principal.has(permission):
        raise deny(request, principal, permission, audit, clock)
