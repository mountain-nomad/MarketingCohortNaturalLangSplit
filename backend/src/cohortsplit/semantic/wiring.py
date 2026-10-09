"""Install the semantic-context and crawler APIs into the FastAPI application."""

from fastapi import FastAPI
from sqlalchemy import Engine

from cohortsplit.auth.crawler_integration import RoleExportGrants
from cohortsplit.auth.dependencies import AuthState
from cohortsplit.config import Settings
from cohortsplit.crawler.settings import load_crawler_settings
from cohortsplit.semantic import api
from cohortsplit.semantic.crawl import CrawlCoordinator, CrawlEnvironment
from cohortsplit.semantic.service import SemanticContextService
from cohortsplit.warehouse.adapter import WarehouseAdapter
from cohortsplit.warehouse.factory import create_warehouse_adapter


def install_semantic(app: FastAPI, settings: Settings, engine: Engine) -> None:
    """Register semantic state and routers. Requires :func:`install_auth` to have run."""
    auth: AuthState = app.state.auth

    def adapter() -> WarehouseAdapter:
        return create_warehouse_adapter(settings)  # read-only role only (FR-1)

    app.state.semantic = api.SemanticState(
        service=SemanticContextService(auth.audit),
        crawler_settings=load_crawler_settings,
        export_grants=RoleExportGrants(auth.session_factory),
        coordinator=CrawlCoordinator(engine, auth.audit),
        crawl_environment=CrawlEnvironment(
            adapter_factory=adapter, crawler_settings=load_crawler_settings
        ),
    )
    app.include_router(api.router)
    app.include_router(api.crawler_router)
