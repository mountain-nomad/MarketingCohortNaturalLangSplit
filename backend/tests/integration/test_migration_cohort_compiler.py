"""Migration 0005_cohort_compiler: single head, tables, draft-0 data migration and its
lossless downgrade (crawler ruling R1, ruling C2)."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from cohortsplit.config import Settings
from cohortsplit.db import create_appdb_engine
from tests.integration.conftest import ALEMBIC_INI
from tests.unit.cohort_spec.test_draft import SPEC as DRAFT_SPEC
from tests.unit.cohort_spec.test_upgrade import EXPECTED_V1

pytestmark = pytest.mark.integration

REVISION = "0005_cohort_compiler"
TABLES = {"cohort_interpretations", "prepared_cohorts"}
V1_SPEC: dict[str, Any] = {
    "spec_version": "1",
    "entity": "user",
    "where": {"type": "metric", "term": "purchase"},
}


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


def _insert_use_case(engine: Engine, key: str, spec: dict[str, Any], status: str) -> int:
    with engine.begin() as conn:
        return int(
            conn.execute(
                text(
                    "INSERT INTO example_use_cases (use_case_key, origin, status, nl_request, "
                    "spec, spec_version, referenced_columns) VALUES (:key, 'human', :status, "
                    "'n', CAST(:spec AS jsonb), :version, CAST('[]' AS jsonb)) RETURNING id"
                ),
                {
                    "key": key,
                    "status": status,
                    "spec": json.dumps(spec),
                    "version": spec["spec_version"],
                },
            ).scalar_one()
        )


def _use_case(engine: Engine, use_case_id: int) -> Any:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT spec, spec_version, status, referenced_columns, review_note "
                "FROM example_use_cases WHERE id = :id"
            ),
            {"id": use_case_id},
        ).one()


def _clear_use_cases(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM example_use_cases"))


def test_cohort_compiler_revision_is_on_the_single_chain(cfg: Config) -> None:
    # 0006_experiment_split follows this revision; the head itself is pinned by the
    # newest migration's test.
    script = ScriptDirectory.from_config(cfg)

    assert len(script.get_heads()) == 1
    assert REVISION in {rev.revision for rev in script.walk_revisions()}
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == "0004_semantic_context"


def test_upgrade_creates_and_downgrade_removes_tables(cfg: Config, appdb: Engine) -> None:
    command.upgrade(cfg, REVISION)
    assert _tables(appdb) >= TABLES

    command.downgrade(cfg, "0004_semantic_context")
    assert not TABLES & _tables(appdb)

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    assert _tables(appdb) >= TABLES


def test_draft0_use_cases_are_migrated_to_v1_and_back(cfg: Config, appdb: Engine) -> None:
    command.downgrade(cfg, "0004_semantic_context")
    _clear_use_cases(appdb)
    draft_id = _insert_use_case(appdb, "draft", DRAFT_SPEC, "confirmed")

    command.upgrade(cfg, REVISION)
    upgraded = _use_case(appdb, draft_id)
    assert upgraded.spec_version == "1"
    assert upgraded.spec == EXPECTED_V1
    assert upgraded.status == "confirmed"
    assert set(upgraded.referenced_columns) >= {
        "public.users.is_active",
        "public.orders.user_id",
        "public.order_items.discount",
    }

    v1_id = _insert_use_case(appdb, "v1-only", V1_SPEC, "confirmed")
    command.downgrade(cfg, "0004_semantic_context")

    restored = _use_case(appdb, draft_id)
    assert restored.spec_version == "draft-0"
    assert restored.spec == DRAFT_SPEC
    assert restored.status == "confirmed"
    # A spec written as v1 has no draft-0 form: it is kept and flagged for re-review.
    v1_only = _use_case(appdb, v1_id)
    assert v1_only.status == "needs_rereview"
    assert v1_only.review_note is not None

    _clear_use_cases(appdb)
    command.upgrade(cfg, "head")


def test_downgrade_never_pairs_an_edited_request_with_the_old_spec(
    cfg: Config, appdb: Engine
) -> None:
    """Review finding 3: a use case edited after the upgrade (new request, v1 spec) must not
    get its pre-edit draft-0 spec back while staying confirmed (BR-7)."""
    command.downgrade(cfg, "0004_semantic_context")
    _clear_use_cases(appdb)
    edited_id = _insert_use_case(appdb, "edited", DRAFT_SPEC, "confirmed")
    command.upgrade(cfg, REVISION)
    with appdb.begin() as conn:
        conn.execute(
            text(
                "UPDATE example_use_cases SET nl_request = 'Edited request', "
                "spec = CAST(:spec AS jsonb) WHERE id = :id"
            ),
            {"id": edited_id, "spec": json.dumps(V1_SPEC)},
        )

    command.downgrade(cfg, "0004_semantic_context")

    row = _use_case(appdb, edited_id)
    assert row.spec == V1_SPEC
    assert row.spec_version == "1"
    assert row.status == "needs_rereview"

    _clear_use_cases(appdb)
    command.upgrade(cfg, "head")
