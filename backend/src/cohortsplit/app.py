"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import Engine

from cohortsplit import __version__
from cohortsplit.auth.wiring import install_auth
from cohortsplit.config import Settings, load_settings
from cohortsplit.db import AppDatabaseUnavailableError, check_appdb, create_appdb_engine
from cohortsplit.db import describe_location as describe_db

logger = logging.getLogger("cohortsplit")


def _configure_logging(level: str) -> None:
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)


def _api_router(engine: Engine) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health() -> dict[str, str]:
        """Liveness: the process is up. Touches no database."""
        return {"status": "ok"}

    @router.get("/ready", response_model=None)
    def ready() -> dict[str, object] | JSONResponse:
        """Readiness: fails closed (503) when the application database is unreachable."""
        try:
            check_appdb(engine)
        except AppDatabaseUnavailableError as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "checks": {"appdb": "unavailable"},
                    "error": str(exc),
                },
            )
        return {"status": "ok", "checks": {"appdb": "ok"}}

    return router


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built SPA: real files from ``dist``, otherwise ``index.html``."""
    root = dist.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (root / path).resolve()
        if path and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(settings: Settings | None = None, *, engine: Engine | None = None) -> FastAPI:
    settings = settings or load_settings()
    _configure_logging(settings.log_level)
    engine = engine or create_appdb_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "cohortsplit starting version=%s appdb=%s appdb_user=%s "
            "warehouse_configured=%s frontend_dist=%s",
            __version__,
            describe_db(engine.url),
            settings.appdb_user,
            settings.warehouse_dsn is not None,
            settings.frontend_dist,
        )
        yield
        engine.dispose()

    app = FastAPI(
        title="CohortSplit",
        version=__version__,
        lifespan=lifespan,
        # API docs are not public; install_auth serves them to signed-in users when enabled.
        docs_url=None,
        openapi_url=None,
        redoc_url=None,
    )
    app.include_router(_api_router(engine))
    install_auth(app, settings, engine)
    if settings.frontend_dist is not None:
        _mount_frontend(app, settings.frontend_dist)
    return app
