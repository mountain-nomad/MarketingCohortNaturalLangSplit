"""Interactive API docs and the OpenAPI schema are not public (ruling 6)."""

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from tests.auth.helpers import ApiClient, FakeClock, make_user


@pytest.mark.parametrize("path", ["/api/docs", "/api/openapi.json", "/docs", "/redoc"])
def test_docs_disabled_by_default(app: FastAPI, path: str) -> None:
    assert ApiClient(app).get(path).status_code == 404


@pytest.mark.parametrize("settings_overrides", [{"api_docs_enabled": True}])
@pytest.mark.parametrize("path", ["/api/docs", "/api/openapi.json"])
def test_enabled_docs_require_a_session(app: FastAPI, path: str) -> None:
    assert ApiClient(app).get(path).status_code == 401


@pytest.mark.parametrize("settings_overrides", [{"api_docs_enabled": True}])
def test_enabled_docs_served_to_authenticated_users(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    make_user(db, "dev@example.com", now=clock.now)
    client = ApiClient(app)
    client.login("dev@example.com")

    schema = client.get("/api/openapi.json")
    docs = client.get("/api/docs")

    assert schema.status_code == 200
    assert "/api/auth/login" in schema.json()["paths"]
    assert docs.status_code == 200
    assert "/api/openapi.json" in docs.text
