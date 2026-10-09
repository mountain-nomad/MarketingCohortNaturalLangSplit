"""FastAPI application factory."""

from fastapi import FastAPI

from cohortsplit.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    raise NotImplementedError
