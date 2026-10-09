"""Migration 0004_semantic_context: single head, tables, constraints, clean downgrade."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from cohortsplit.config import Settings
from cohortsplit.db import create_appdb_engine
from tests.integration.conftest import ALEMBIC_INI

pytestmark = pytest.mark.integration

REVISION = "0004_semantic_context"
TABLES = {"business_context_entries", "semantic_versions"}
REVIEW_COLUMNS = {"reviewed_by", "reviewed_at"}


@pytest.fixture
def cfg(appdb_settings: Settings) -> Iterator[Config]:
    config = Config(str(ALEMBIC_INI))
    yield config
    command.upgrade(config, "head")


@pytest.fixture
def appdb(appdb_settings: Settings) -> Iterator[Engine]:
    engine = create_appdb_engine(appdb_settings)
    yield engine
    engine.dispose()


def _use_case_columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {c["name"] for c in inspect(conn).get_columns("example_use_cases")}


def _tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


def test_single_head_is_the_semantic_context_revision(cfg: Config) -> None:
    script = ScriptDirectory.from_config(cfg)

    assert script.get_heads() == [REVISION]
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == "0002_auth"


def test_upgrade_creates_and_downgrade_removes(cfg: Config, appdb: Engine) -> None:
    command.upgrade(cfg, REVISION)
    assert _tables(appdb) >= TABLES
    assert _use_case_columns(appdb) >= REVIEW_COLUMNS

    command.downgrade(cfg, "0002_auth")
    assert not TABLES & _tables(appdb)
    assert not REVIEW_COLUMNS & _use_case_columns(appdb)

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    assert _tables(appdb) >= TABLES


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO business_context_entries (key, kind, synonyms, description, definition, "
        "created_at, updated_at) VALUES ('k', 'sql', '[]', '', '{}', now(), now())",
        "INSERT INTO business_context_entries (key, kind, synonyms, description, definition, "
        "created_at, updated_at) VALUES ('Bad Key', 'term', '[]', '', '{}', now(), now())",
        "INSERT INTO semantic_versions (version, components, cause, actor_type, created_at) "
        "VALUES ('v', '{}', 'c', 'robot', now())",
    ],
    ids=["unknown-kind", "bad-key", "unknown-actor-type"],
)
def test_constraints(cfg: Config, appdb: Engine, statement: str) -> None:
    command.upgrade(cfg, "head")
    with pytest.raises(IntegrityError), appdb.begin() as conn:
        conn.execute(text(statement))


def test_business_context_key_unique(cfg: Config, appdb: Engine) -> None:
    command.upgrade(cfg, "head")
    insert = text(
        "INSERT INTO business_context_entries (key, kind, synonyms, description, definition, "
        "created_at, updated_at) VALUES ('dup_key', 'term', '[]', '', '{}', now(), now())"
    )
    with appdb.begin() as conn:
        conn.execute(text("DELETE FROM business_context_entries WHERE key = 'dup_key'"))
        conn.execute(insert)
    try:
        with pytest.raises(IntegrityError), appdb.begin() as conn:
            conn.execute(insert)
    finally:
        with appdb.begin() as conn:
            conn.execute(text("DELETE FROM business_context_entries WHERE key = 'dup_key'"))
