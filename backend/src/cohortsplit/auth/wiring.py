"""Install authentication, authorization and audit into the FastAPI application."""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import Engine
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import sessionmaker
from starlette.responses import Response

from cohortsplit.audit.service import AuditService, AuditWriteError
from cohortsplit.auth import api as auth_api
from cohortsplit.auth.dependencies import AuthState, CurrentPrincipal
from cohortsplit.auth.errors import ApiError, ServiceUnavailableError
from cohortsplit.auth.policy import PolicyService
from cohortsplit.auth.service import AuthService
from cohortsplit.config import Settings

logger = logging.getLogger(__name__)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _error_response(error: ApiError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error.body(), headers=error.headers)


async def _api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApiError):  # pragma: no cover - registered for ApiError only
        raise exc
    return _error_response(exc)


async def _validation_handler(_: Request, exc: Exception) -> JSONResponse:
    """422 without echoing submitted values (they may be passwords)."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    fields = [{"loc": list(e.get("loc", ())), "msg": str(e.get("msg", ""))} for e in errors]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "The request is invalid.",
                "fields": fields,
            }
        },
    )


async def _db_unavailable_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.warning("appdb unavailable during request: %s", type(exc).__name__)
    return _error_response(ServiceUnavailableError())


async def _audit_unavailable_handler(_: Request, exc: Exception) -> JSONResponse:
    return _error_response(
        ServiceUnavailableError(
            "audit_unavailable",
            "The action was refused because it could not be recorded in the audit log.",
        )
    )


def _docs_router(app: FastAPI) -> APIRouter:
    router = APIRouter(prefix="/api", include_in_schema=False)

    @router.get("/openapi.json")
    def openapi(_: CurrentPrincipal) -> JSONResponse:
        return JSONResponse(app.openapi())

    @router.get("/docs")
    def docs(_: CurrentPrincipal) -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title=f"{app.title} API")

    return router


def install_auth(app: FastAPI, settings: Settings, engine: Engine) -> None:
    """Register auth state, error handlers, request ids and the auth/admin/audit routers.

    Must run before any catch-all route (the SPA fallback) is added.
    """
    session_factory = sessionmaker(engine, expire_on_commit=False)
    audit = AuditService(session_factory)
    app.state.auth = AuthState(
        settings=settings,
        session_factory=session_factory,
        audit=audit,
        policy=PolicyService(),
        auth=AuthService(settings, audit),
    )

    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    for db_error in (OperationalError, InterfaceError, PoolTimeoutError):
        app.add_exception_handler(db_error, _db_unavailable_handler)
    app.add_exception_handler(AuditWriteError, _audit_unavailable_handler)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(auth_api.router)
    if settings.api_docs_enabled:
        app.include_router(_docs_router(app))
