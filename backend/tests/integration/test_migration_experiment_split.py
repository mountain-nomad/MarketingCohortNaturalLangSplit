"""Migration 0006_experiment_split: single head, run tables whose membership snapshot holds
only canonical ids and groups (AC-21c), constraints, and a clean downgrade."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from cohortsplit.config import Settings
from cohortsplit.db import create_appdb_engine
from tests.integration.conftest import ALEMBIC_INI

pytestmark = pytest.mark.integration

REVISION = "0006_experiment_split"
TABLES = {"experiment_runs", "experiment_run_members"}


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


def _tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


def test_single_head_is_the_experiment_split_revision(cfg: Config) -> None:
    script = ScriptDirectory.from_config(cfg)

    assert script.get_heads() == [REVISION]
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == "0005_cohort_compiler"


def test_upgrade_creates_and_downgrade_removes_tables(cfg: Config, appdb: Engine) -> None:
    command.upgrade(cfg, REVISION)
    assert _tables(appdb) >= TABLES

    command.downgrade(cfg, "0005_cohort_compiler")
    assert not TABLES & _tables(appdb)

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    assert _tables(appdb) >= TABLES


def test_member_table_columns(cfg: Config, appdb: Engine) -> None:
    command.upgrade(cfg, "head")
    with appdb.connect() as conn:
        columns = {c["name"] for c in inspect(conn).get_columns("experiment_run_members")}
        primary_key = inspect(conn).get_pk_constraint("experiment_run_members")

    assert columns == {"run_id", "position", "canonical_user_id", "assigned_group"}
    assert primary_key["constrained_columns"] == ["run_id", "position"]


def _insert_run(conn: sa.Connection, run_id: str, **overrides: object) -> None:
    user_id = conn.execute(
        text(
            "INSERT INTO users (email, display_name, password_hash, must_change_password, "
            "is_active, created_at, updated_at) VALUES (:email, 'm', 'x', false, true, :now, "
            ":now) RETURNING id"
        ),
        {"email": f"{run_id}@example.com", "now": datetime(2026, 1, 1, tzinfo=UTC)},
    ).scalar_one()
    values: dict[str, object] = {
        "id": run_id,
        "created_by": user_id,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "as_of": datetime(2026, 1, 1, tzinfo=UTC),
        "test_percent": 50,
        "control_percent": 50,
        "experiment_type": "ab",
        "user_id_type": "integer",
    }
    values.update(overrides)
    conn.execute(
        text(
            "INSERT INTO experiment_runs (id, created_by, created_at, prepared_cohort_id, "
            "nl_request, spec, spec_hash, sql, sql_params, sql_hash, dialect, as_of, "
            "semantic_version, canonical_user_id, user_id_type, experiment_key, "
            "experiment_type, test_percent, control_percent, assignment_version, test_count, "
            "control_count, export_columns, download_count) VALUES (:id, :created_by, "
            ":created_at, NULL, 'n', '{}', 'h', 's', '{}', 'h', 'postgresql', :as_of, 'v', "
            "'public.users.user_id', :user_id_type, 'k', :experiment_type, :test_percent, "
            ":control_percent, 1, 0, 0, '[]', 0)"
        ),
        values,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"test_percent": 60, "control_percent": 50},
        {"test_percent": 0, "control_percent": 100},
        {"experiment_type": "abc"},
        {"user_id_type": "float"},
    ],
)
def test_run_constraints(cfg: Config, appdb: Engine, overrides: dict[str, object]) -> None:
    command.upgrade(cfg, "head")
    with pytest.raises(sa.exc.IntegrityError), appdb.begin() as conn:
        _insert_run(conn, "a" * 32, **overrides)


def test_group_constraint(cfg: Config, appdb: Engine) -> None:
    command.upgrade(cfg, "head")
    run_id = "b" * 32
    try:
        with appdb.begin() as conn:
            _insert_run(conn, run_id)
        with pytest.raises(sa.exc.IntegrityError), appdb.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO experiment_run_members (run_id, position, canonical_user_id, "
                    "assigned_group) VALUES (:id, 0, '1', 'treatment')"
                ),
                {"id": run_id},
            )
    finally:
        with appdb.begin() as conn:
            conn.execute(text("DELETE FROM experiment_runs WHERE id = :id"), {"id": run_id})
            conn.execute(text("DELETE FROM users WHERE email = :e"), {"e": f"{run_id}@example.com"})
