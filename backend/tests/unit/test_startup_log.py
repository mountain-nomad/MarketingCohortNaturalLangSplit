"""The startup log line describes the configuration without secrets."""

import logging

import pytest
from fastapi.testclient import TestClient

from cohortsplit.app import create_app
from cohortsplit.config import load_settings
from tests.constants import APPDB_PASSWORD, WAREHOUSE_PASSWORD


def test_startup_log_line_has_no_password(
    unreachable_appdb_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    with TestClient(create_app(load_settings())):
        pass

    startup = [r for r in caplog.records if r.name.startswith("cohortsplit")]
    assert startup, "expected a startup log line from the cohortsplit logger"
    assert any("127.0.0.1" in r.getMessage() for r in startup)
    assert APPDB_PASSWORD not in caplog.text
    assert WAREHOUSE_PASSWORD not in caplog.text
