"""Readiness fails closed with an actionable error when appdb is unreachable."""

import time

import pytest
from fastapi.testclient import TestClient

from cohortsplit.app import create_app
from cohortsplit.config import load_settings
from tests.constants import APPDB_PASSWORD, WAREHOUSE_PASSWORD


def test_ready_returns_503_when_appdb_unreachable(
    unreachable_appdb_env: pytest.MonkeyPatch, closed_port: int
) -> None:
    client = TestClient(create_app(load_settings()))

    started = time.monotonic()
    response = client.get("/api/ready")
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"] == {"appdb": "unavailable"}
    # Actionable: says where it tried to connect and which settings to check.
    assert f"127.0.0.1:{closed_port}/cohortsplit" in body["error"]
    assert "COHORTSPLIT_APPDB_" in body["error"]
    assert elapsed < 10


def test_ready_error_does_not_leak_secrets(unreachable_appdb_env: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(load_settings()))

    response = client.get("/api/ready")

    assert APPDB_PASSWORD not in response.text
    assert WAREHOUSE_PASSWORD not in response.text
