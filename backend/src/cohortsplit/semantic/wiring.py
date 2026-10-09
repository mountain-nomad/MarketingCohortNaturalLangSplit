"""Install the semantic-context API into the FastAPI application."""

from fastapi import FastAPI

from cohortsplit.auth.crawler_integration import RoleExportGrants
from cohortsplit.auth.dependencies import AuthState
from cohortsplit.crawler.settings import load_crawler_settings
from cohortsplit.semantic import api
from cohortsplit.semantic.service import SemanticContextService


def install_semantic(app: FastAPI) -> None:
    """Register semantic state and routers. Requires :func:`install_auth` to have run."""
    auth: AuthState = app.state.auth
    app.state.semantic = api.SemanticState(
        service=SemanticContextService(auth.audit),
        crawler_settings=load_crawler_settings,
        export_grants=RoleExportGrants(auth.session_factory),
    )
    app.include_router(api.router)
