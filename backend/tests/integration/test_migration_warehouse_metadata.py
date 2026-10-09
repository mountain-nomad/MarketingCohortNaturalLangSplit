"""Migration 0003_warehouse_metadata: crawler tables, constraints, clean downgrade."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from cohortsplit.config import Settings
from cohortsplit.db import create_appdb_engine
from tests.integration.conftest import ALEMBIC_INI, CRAWLER_TABLES

pytestmark = pytest.mark.integration


@pytest.fixture
def cfg(appdb_settings: Settings) -> Iterator[Config]:
    config = Config(str(ALEMBIC_INI))
    yield config
    command.upgrade(config, "head")


def test_revision_identity(cfg: Config) -> None:
    revision = ScriptDirectory.from_config(cfg).get_revision("0003_warehouse_metadata")

    assert revision is not None
    assert revision.down_revision is not None


def test_upgrade_creates_and_downgrade_drops_crawler_tables(
    cfg: Config, appdb_settings: Settings
) -> None:
    engine = create_appdb_engine(appdb_settings)
    try:
        command.upgrade(cfg, "0003_warehouse_metadata")
        with engine.connect() as conn:
            assert set(CRAWLER_TABLES) <= set(inspect(conn).get_table_names())

        command.downgrade(cfg, "0003_warehouse_metadata-1")
        with engine.connect() as conn:
            assert not set(CRAWLER_TABLES) & set(inspect(conn).get_table_names())
    finally:
        engine.dispose()


def test_constraints(cfg: Config, appdb_settings: Settings) -> None:
    command.upgrade(cfg, "head")
    engine = create_appdb_engine(appdb_settings)
    insert = text(
        "INSERT INTO example_use_cases (use_case_key, origin, status, nl_request, spec, "
        "spec_version) VALUES (:key, :origin, :status, 'n', '{}'::jsonb, 'draft-0')"
    )
    try:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE example_use_cases, semantic_docs, crawl_runs CASCADE"))
        for params in (
            {"key": "k", "origin": "generated", "status": "approved"},
            {"key": "k", "origin": "robot", "status": "pending_review"},
        ):
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(insert, params)
        with engine.begin() as conn:
            conn.execute(insert, {"key": "k", "origin": "generated", "status": "pending_review"})
            # Human use cases may share a key with generated ones.
            conn.execute(insert, {"key": "k", "origin": "human", "status": "confirmed"})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(insert, {"key": "k", "origin": "generated", "status": "confirmed"})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text("INSERT INTO crawl_runs (status) VALUES ('exploded')"))
    finally:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE example_use_cases, semantic_docs, crawl_runs CASCADE"))
        engine.dispose()
