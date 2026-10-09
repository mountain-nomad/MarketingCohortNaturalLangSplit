"""Liveness endpoint works without any database."""

import pytest
from fastapi.testclient import TestClient

from cohortsplit.app import create_app
from cohortsplit.config import load_settings


def test_health_returns_ok_without_database(unreachable_appdb_env: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(load_settings()))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
