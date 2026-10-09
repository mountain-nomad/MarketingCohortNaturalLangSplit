"""Integration fixtures: real appdb and warehouse from ``docker compose`` (``make up``).

Tests skip with a reason when the databases are not reachable, unless
``COHORTSPLIT_REQUIRE_INTEGRATION=1`` (set by ``make test-integration`` and CI),
in which case unreachability is a hard failure.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import Engine, inspect, text

from cohortsplit.config import ConfigError, Settings, load_settings
from cohortsplit.crawler.store import CrawlStore
from cohortsplit.db import create_appdb_engine
from cohortsplit.warehouse import PostgresWarehouseAdapter, ReadOnlyExecutor

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
CRAWLER_TABLES = ("example_use_cases", "semantic_docs", "crawl_runs")


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


@pytest.fixture(scope="session")
def warehouse_admin_dsn() -> str:
    """TEST-ONLY admin DSN, used to create scratch schemas. The app never uses it."""
    dsn = os.environ.get("COHORTSPLIT_TEST_WAREHOUSE_ADMIN_DSN")
    if not dsn:
        _unavailable("COHORTSPLIT_TEST_WAREHOUSE_ADMIN_DSN is not set")
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            pass
    except psycopg.OperationalError as exc:
        _unavailable(f"warehouse admin: {type(exc).__name__}")
    return dsn


@pytest.fixture(scope="session")
def migrated_appdb(appdb_settings: Settings) -> Iterator[Engine]:
    command.upgrade(Config(str(ALEMBIC_INI)), "head")
    engine = create_appdb_engine(appdb_settings)
    yield engine
    engine.dispose()


@pytest.fixture
def crawl_store(migrated_appdb: Engine) -> CrawlStore:
    """A CrawlStore over empty crawler tables."""
    with migrated_appdb.begin() as conn:
        existing = [t for t in CRAWLER_TABLES if inspect(conn).has_table(t)]
        if existing:
            conn.execute(text(f"TRUNCATE {', '.join(existing)} RESTART IDENTITY CASCADE"))
    return CrawlStore(migrated_appdb)


@pytest.fixture
def ro_adapter(warehouse_ro_dsn: str) -> PostgresWarehouseAdapter:
    return PostgresWarehouseAdapter(ReadOnlyExecutor(SecretStr(warehouse_ro_dsn)))


SCRATCH_DDL = """
CREATE TABLE {s}.users (
    user_id bigint PRIMARY KEY,
    email text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE {s}.products (product_id bigint PRIMARY KEY, name text NOT NULL);
CREATE TABLE {s}.orders (
    order_id bigint PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES {s}.users,
    status text NOT NULL CHECK (status IN ('pending', 'paid', 'delivered')),
    grand_total numeric(12, 2) NOT NULL,
    ordered_at timestamptz NOT NULL
);
CREATE TABLE {s}.order_items (
    order_id bigint REFERENCES {s}.orders,
    product_id bigint REFERENCES {s}.products,
    discount numeric NOT NULL DEFAULT 0,
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE {s}.carts (
    cart_id bigint PRIMARY KEY,
    user_id bigint REFERENCES {s}.users,
    status text NOT NULL
);
INSERT INTO {s}.users (user_id, email)
    SELECT i, 'scratch' || i || '@example.com' FROM generate_series(1, 10) i;
INSERT INTO {s}.products SELECT i, 'Product ' || i FROM generate_series(1, 3) i;
INSERT INTO {s}.orders
    SELECT i, 1 + i % 10, (ARRAY['pending', 'paid', 'delivered', 'delivered'])[1 + i % 4],
           10 * i, now() - make_interval(days => i)
    FROM generate_series(1, 20) i;
INSERT INTO {s}.order_items SELECT i, 1 + i % 3, (i % 2) * 0.1 FROM generate_series(1, 20) i;
INSERT INTO {s}.carts
    SELECT i, 1 + i % 10, (ARRAY['active', 'abandoned', 'abandoned'])[1 + i % 3]
    FROM generate_series(1, 9) i;
GRANT USAGE ON SCHEMA {s} TO cohortsplit_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA {s} TO cohortsplit_ro;
"""


class ScratchWarehouse:
    """A throwaway schema in the demo warehouse, changed with admin rights (tests only)."""

    def __init__(self, admin_dsn: str, schema: str) -> None:
        self.admin_dsn = admin_dsn
        self.schema = schema

    def admin(self, statement: str) -> None:
        """Run DDL/DML as the warehouse admin; ``{s}`` is replaced by the scratch schema."""
        query = sql.SQL(statement).format(s=sql.Identifier(self.schema))
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn:
            conn.execute(query)


@pytest.fixture
def scratch(warehouse_admin_dsn: str, warehouse_ro_dsn: str) -> Iterator[ScratchWarehouse]:
    schema = f"cs_scratch_{uuid.uuid4().hex[:10]}"
    warehouse = ScratchWarehouse(warehouse_admin_dsn, schema)
    warehouse.admin("CREATE SCHEMA {s}")
    try:
        warehouse.admin(SCRATCH_DDL)
        yield warehouse
    finally:
        warehouse.admin("DROP SCHEMA {s} CASCADE")
