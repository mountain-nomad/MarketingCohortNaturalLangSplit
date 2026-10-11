"""Install the experiment API into the FastAPI application."""

from fastapi import FastAPI

from cohortsplit.auth.dependencies import AuthState
from cohortsplit.experiment import api
from cohortsplit.experiment.service import ExperimentService


def install_experiment(app: FastAPI) -> None:
    """Register experiment state and the router. Requires :func:`install_cohort` (the cohort
    environment: semantic snapshot and read-only executor) to have run."""
    auth: AuthState = app.state.auth
    app.state.experiment = api.ExperimentState(service=ExperimentService(auth.audit, auth.policy))
    app.include_router(api.router)
