"""Fixtures for the authentication / RBAC / audit test suite.

Every test that uses ``engine`` runs twice:

* ``sqlite`` — in-memory SQLite built from the ORM metadata (unit run, fast);
* ``postgres`` — a fresh database on the compose appdb, migrated with ``alembic upgrade
  head`` (marked ``integration``; skipped unless the databases are reachable, hard
  failure when ``COHORTSPLIT_REQUIRE_INTEGRATION=1``).
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cohortsplit.app import create_app
from cohortsplit.audit.models import AuditEvent
from cohortsplit.auth import passwords
from cohortsplit.auth.clock import get_clock
from cohortsplit.config import ConfigError, Settings, load_settings
from cohortsplit.db import appdb_url
from cohortsplit.orm import Base
from tests.auth.helpers import (
    FakeClock,
    seed_reference_data,
)

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

BACKENDS = ["sqlite", pytest.param("postgres", marks=pytest.mark.integration)]

# Cheap Argon2id parameters for tests; production parameters are pinned by a dedicated test.
TEST_HASHER = passwords.PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture(autouse=True)
def _cheap_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "_hasher", TEST_HASHER)
    monkeypatch.setattr(passwords, "_dummy_hash", None)


def _unavailable(reason: str) -> NoReturn:
    if os.environ.get("COHORTSPLIT_REQUIRE_INTEGRATION") == "1":
        pytest.fail(f"integration environment required but unavailable: {reason}")
    pytest.skip(f"integration environment unavailable ({reason}); run `make up` first")


@pytest.fixture(scope="session")
def pg_database() -> Iterator[URL]:
    """A fresh, migrated PostgreSQL database for this test session; dropped afterwards."""
    try:
        base = load_settings()
    except ConfigError as exc:
        _unavailable(str(exc))
    admin_kwargs = {
        "host": base.appdb_host,
        "port": base.appdb_port,
        "dbname": base.appdb_name,
        "user": base.appdb_user,
        "password": base.appdb_password.get_secret_value(),
        "connect_timeout": 3,
        "autocommit": True,
    }
    name = f"cohortsplit_test_{uuid.uuid4().hex[:12]}"
    try:
        with psycopg.connect(**admin_kwargs) as conn:  # type: ignore[arg-type]
            conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as exc:
        _unavailable(f"appdb: {type(exc).__name__}")

    previous = os.environ.get("COHORTSPLIT_APPDB_NAME")
    os.environ["COHORTSPLIT_APPDB_NAME"] = name
    try:
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
    finally:
        if previous is None:
            del os.environ["COHORTSPLIT_APPDB_NAME"]
        else:
            os.environ["COHORTSPLIT_APPDB_NAME"] = previous

    yield appdb_url(base).set(database=name)

    with psycopg.connect(**admin_kwargs) as conn:  # type: ignore[arg-type]
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _sqlite_engine() -> Engine:
    engine = create_engine(
        "sqlite+pysqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db, datetime(2026, 1, 1, tzinfo=UTC))
        db.commit()
    return engine


_PG_CLEANUP = """
ALTER TABLE audit_events DISABLE TRIGGER USER;
DELETE FROM audit_events;
ALTER TABLE audit_events ENABLE TRIGGER USER;
DELETE FROM auth_sessions;
DELETE FROM login_throttles;
DELETE FROM user_roles;
DELETE FROM role_export_columns;
DELETE FROM role_permissions;
DELETE FROM roles WHERE NOT is_system;
DELETE FROM users;
"""


@pytest.fixture(params=BACKENDS)
def engine(request: pytest.FixtureRequest) -> Iterator[Engine]:
    if request.param == "sqlite":
        eng = _sqlite_engine()
        yield eng
        eng.dispose()
        return
    url: URL = request.getfixturevalue("pg_database")
    eng = create_engine(url, connect_args={"connect_timeout": 3})
    with eng.begin() as conn:
        for statement in filter(None, (s.strip() for s in _PG_CLEANUP.split(";"))):
            conn.execute(text(statement))
    yield eng
    eng.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 3, 2, 9, 0, tzinfo=UTC))


@pytest.fixture
def settings_overrides() -> dict[str, object]:
    """Tests override individual auth settings by redefining this fixture or mutating it."""
    return {}


@pytest.fixture
def settings(settings_overrides: dict[str, object]) -> Settings:
    values: dict[str, object] = {
        "appdb_password": SecretStr("unused-in-auth-tests"),
        "session_idle_timeout_minutes": 480,
        "session_max_lifetime_hours": 168,
        "login_max_failures": 5,
        "login_failure_window_minutes": 15,
        "login_lockout_minutes": 15,
        "cookie_secure": None,
        "api_docs_enabled": False,
        "frontend_dist": None,
    }
    values.update(settings_overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def app(engine: Engine, settings: Settings, clock: FakeClock) -> FastAPI:
    application = create_app(settings, engine=engine)
    application.dependency_overrides[get_clock] = lambda: clock
    return application


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        yield session


@pytest.fixture
def audit_events(engine: Engine) -> "AuditReader":
    return AuditReader(engine)


class AuditReader:
    """Reads audit events straight from the table (independent of the audit API)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def all(self) -> list[AuditEvent]:
        with Session(self._engine) as session:
            return list(session.query(AuditEvent).order_by(AuditEvent.id))

    def actions(self) -> list[str]:
        return [e.action for e in self.all()]

    def of(self, action: str) -> list[AuditEvent]:
        return [e for e in self.all() if e.action == action]
