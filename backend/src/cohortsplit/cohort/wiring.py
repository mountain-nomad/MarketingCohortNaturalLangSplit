"""Install the cohort API into the FastAPI application."""

from fastapi import FastAPI

from cohortsplit.auth.dependencies import AuthState
from cohortsplit.cohort import api
from cohortsplit.cohort.environment import default_environment
from cohortsplit.cohort.service import CohortService
from cohortsplit.config import Settings


def install_cohort(app: FastAPI, settings: Settings) -> None:
    """Register cohort state and the router. Requires :func:`install_auth` to have run."""
    auth: AuthState = app.state.auth
    app.state.cohort = api.CohortState(
        service=CohortService(auth.audit, auth.policy, settings),
        environment=default_environment(settings, auth.session_factory),
    )
    app.include_router(api.router)
