"""Integration fixtures: real appdb and warehouse from ``docker compose`` (``make up``).

Tests skip with a reason when the databases are not reachable, unless
``COHORTSPLIT_REQUIRE_INTEGRATION=1`` (set by ``make test-integration`` and CI),
in which case unreachability is a hard failure.
"""

import os
from typing import NoReturn

import psycopg
import pytest

from cohortsplit.config import ConfigError, Settings, load_settings


def _unavailable(reason: str) -> NoReturn:
    if os.environ.get("COHORTSPLIT_REQUIRE_INTEGRATION") == "1":
        pytest.fail(f"integration environment required but unavailable: {reason}")
    pytest.skip(f"integration environment unavailable ({reason}); run `make up` first")


@pytest.fixture(scope="session")
def appdb_settings() -> Settings:
    try:
        settings = load_settings()
    except ConfigError as exc:
        _unavailable(str(exc))
    try:
        with psycopg.connect(
            host=settings.appdb_host,
            port=settings.appdb_port,
            dbname=settings.appdb_name,
            user=settings.appdb_user,
            password=settings.appdb_password.get_secret_value(),
            connect_timeout=3,
        ):
            pass
    except psycopg.OperationalError as exc:
        _unavailable(
            f"appdb at {settings.appdb_host}:{settings.appdb_port}/{settings.appdb_name}: "
            f"{type(exc).__name__}"
        )
    return settings


@pytest.fixture(scope="session")
def warehouse_ro_dsn() -> str:
    """DSN for the read-only warehouse role ``cohortsplit_ro``."""
    dsn = os.environ.get("COHORTSPLIT_WAREHOUSE_DSN")
    if not dsn:
        _unavailable("COHORTSPLIT_WAREHOUSE_DSN is not set")
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            pass
    except psycopg.OperationalError as exc:
        _unavailable(f"warehouse: {type(exc).__name__}")
    return dsn
