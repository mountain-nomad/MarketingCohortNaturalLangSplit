"""Readiness succeeds when appdb is reachable."""

import pytest
from fastapi.testclient import TestClient

from cohortsplit.app import create_app
from cohortsplit.config import Settings

pytestmark = pytest.mark.integration


def test_ready_returns_200_when_appdb_reachable(appdb_settings: Settings) -> None:
    client = TestClient(create_app(appdb_settings))

    response = client.get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"appdb": "ok"}}
