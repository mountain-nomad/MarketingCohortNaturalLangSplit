"""Fixtures for the semantic-context API tests.

Reuses the auth suite's fixtures (app, sessions, clock, audit reader). The crawler tables
are PostgreSQL-only (JSONB, advisory locks), so ``engine`` here is the migrated PostgreSQL
test database only (marked ``integration``). The endpoint-security matrix re-imports the
two-backend ``engine`` so 401/403 checks also run on SQLite.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import URL

from tests.auth.conftest import *  # noqa: F403  (shared auth fixtures)
from tests.auth.conftest import _PG_CLEANUP
from tests.integration.conftest import (  # noqa: F401  (warehouse fixtures for crawl tests)
    scratch,
    warehouse_admin_dsn,
    warehouse_ro_dsn,
)

SEMANTIC_TABLES = (
    "business_context_entries",
    "semantic_versions",
    "example_use_cases",
    "semantic_docs",
    "crawl_runs",
)


def clean_semantic_tables(engine: Engine) -> None:
    """Empty semantic and crawler tables (they reference users), then the auth tables."""
    with engine.begin() as conn:
        existing = [t for t in SEMANTIC_TABLES if inspect(conn).has_table(t)]
        if existing:
            conn.execute(text(f"TRUNCATE {', '.join(existing)} RESTART IDENTITY CASCADE"))
        for statement in filter(None, (s.strip() for s in _PG_CLEANUP.split(";"))):
            conn.execute(text(statement))


@pytest.fixture(params=[pytest.param("postgres", marks=pytest.mark.integration)])
def engine(pg_database: URL) -> Iterator[Engine]:
    eng = create_engine(pg_database, connect_args={"connect_timeout": 3})
    clean_semantic_tables(eng)
    yield eng
    clean_semantic_tables(eng)
    eng.dispose()
