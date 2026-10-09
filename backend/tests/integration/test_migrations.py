"""Alembic migrations apply and roll back cleanly against appdb."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from cohortsplit.config import Settings
from cohortsplit.db import create_appdb_engine

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _version_rows(settings: Settings) -> list[str] | None:
    engine = create_appdb_engine(settings)
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("alembic_version"):
                return None
            return [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]
    finally:
        engine.dispose()


def test_upgrade_head_then_downgrade_base(appdb_settings: Settings) -> None:
    cfg = Config(str(ALEMBIC_INI))
    try:
        command.upgrade(cfg, "head")
        assert _version_rows(appdb_settings) == ["0001_baseline"]

        command.downgrade(cfg, "base")
        assert _version_rows(appdb_settings) in (None, [])
    finally:
        # Leave appdb migrated, as the running app expects.
        command.upgrade(cfg, "head")
