"""Alembic migrations apply and roll back cleanly against appdb."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from cohortsplit.config import Settings
from cohortsplit.db import create_appdb_engine

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

AUTH_TABLES = {
    "users",
    "roles",
    "permissions",
    "role_permissions",
    "user_roles",
    "role_export_columns",
    "auth_sessions",
    "login_throttles",
    "audit_events",
}


def _version_rows(settings: Settings) -> list[str] | None:
    engine = create_appdb_engine(settings)
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("alembic_version"):
                return None
            return [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]
    finally:
        engine.dispose()


def _tables(settings: Settings) -> set[str]:
    engine = create_appdb_engine(settings)
    try:
        with engine.connect() as conn:
            return set(inspect(conn).get_table_names())
    finally:
        engine.dispose()


def test_single_head_is_the_auth_revision() -> None:
    heads = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads()

    assert len(heads) == 1
    assert "0002_auth" in {
        rev.revision
        for rev in ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).walk_revisions()
    }


def test_upgrade_head_then_downgrade_base(appdb_settings: Settings) -> None:
    cfg = Config(str(ALEMBIC_INI))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    try:
        command.upgrade(cfg, "head")
        assert _version_rows(appdb_settings) == [head]
        assert _tables(appdb_settings) >= AUTH_TABLES

        command.downgrade(cfg, "base")
        assert _version_rows(appdb_settings) in (None, [])
        assert not (AUTH_TABLES & _tables(appdb_settings))

        # Round trip: upgrading again after a full downgrade works.
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0001_baseline")
        assert _version_rows(appdb_settings) == ["0001_baseline"]
        assert not (AUTH_TABLES & _tables(appdb_settings))
    finally:
        # Leave appdb migrated, as the running app expects.
        command.upgrade(cfg, "head")
