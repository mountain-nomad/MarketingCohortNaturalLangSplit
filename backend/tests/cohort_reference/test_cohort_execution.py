"""Compiler path against a real warehouse: guest carts never match (AC-2), the timeout and
row-cap errors of the Failure Behavior table, execution only through the read-only role
(integration, scratch schema)."""

import threading
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from cohortsplit.warehouse import ReadOnlyExecutor
from tests.auth.helpers import FakeClock, error_code
from tests.cohort.helpers import INTERPRET, PREVIEW, confirm
from tests.cohort_reference.world import World, build_world, metric, paid_statuses, reference
from tests.integration.conftest import ScratchWarehouse
from tests.llm_fakes import interpretation

pytestmark = pytest.mark.integration


def entries(schema: str) -> list[dict[str, Any]]:
    return [
        {
            "key": "customer_id",
            "definition": {"kind": "canonical_user_id", "column": f"{schema}.users.user_id"},
        },
        metric("paid_order", schema, [paid_statuses(schema)]),
    ]


def abandoned_carts(schema: str) -> dict[str, Any]:
    return {
        "spec_version": "1",
        "where": {
            "type": "related",
            "table": f"{schema}.carts",
            "path": [
                {"from_column": f"{schema}.users.user_id", "to_column": f"{schema}.carts.user_id"}
            ],
            "filters": [
                {"column": f"{schema}.carts.status", "operator": "=", "value": "abandoned"}
            ],
        },
    }


@pytest.fixture
def guest_carts(scratch: ScratchWarehouse) -> ScratchWarehouse:
    scratch.admin(
        "INSERT INTO {s}.carts (cart_id, user_id, status) "
        "SELECT 100 + i, NULL, 'abandoned' FROM generate_series(1, 5) i"
    )
    return scratch


def scratch_world(
    app: FastAPI,
    engine: Engine,
    db: Session,
    clock: FakeClock,
    scratch: ScratchWarehouse,
    ro_dsn: str,
    executor: ReadOnlyExecutor | None = None,
) -> World:
    return build_world(
        app,
        engine,
        db,
        clock,
        ro_dsn=ro_dsn,
        schema=scratch.schema,
        executor=executor,
        entries=entries(scratch.schema),
    )


def test_ac2_guest_carts_never_match(
    app: FastAPI,
    engine: Engine,
    db: Session,
    clock: FakeClock,
    guest_carts: ScratchWarehouse,
    warehouse_ro_dsn: str,
) -> None:
    schema = guest_carts.schema
    world = scratch_world(app, engine, db, clock, guest_carts, warehouse_ro_dsn)
    guests = reference(
        warehouse_ro_dsn,
        sql.SQL("SELECT cart_id FROM {}.carts WHERE user_id IS NULL AND status = 'abandoned'")
        .format(sql.Identifier(schema))
        .as_string(),
    )
    assert len(guests) == 5
    expected = reference(
        warehouse_ro_dsn,
        sql.SQL(
            "SELECT u.user_id FROM {s}.users u WHERE EXISTS (SELECT 1 FROM {s}.carts c "
            "WHERE c.user_id = u.user_id AND c.status = 'abandoned')"
        )
        .format(s=sql.Identifier(schema))
        .as_string(),
    )

    result = world.run("Users who abandoned a cart", abandoned_carts(schema))
    members = world.members(result["preview"], db, clock)

    assert expected
    assert members == expected
    assert None not in members
    assert result["preview"]["total_count"] == len(expected)


def test_compiler_path_row_cap_error(
    app: FastAPI,
    engine: Engine,
    db: Session,
    clock: FakeClock,
    scratch: ScratchWarehouse,
    warehouse_ro_dsn: str,
) -> None:
    capped = ReadOnlyExecutor(SecretStr(warehouse_ro_dsn), row_cap=2)
    world = scratch_world(app, engine, db, clock, scratch, warehouse_ro_dsn, executor=capped)
    world.llm.replies[:] = [interpretation({"spec_version": "1"})]  # every user (10)
    interpreted = world.client.post(INTERPRET, json={"nl_request": "All users"}).json()

    response = world.client.post(PREVIEW, json=confirm(interpreted))

    assert response.status_code == 422
    assert error_code(response) == "row_cap_exceeded"
    assert "row cap of 2" in response.json()["error"]["message"]


@pytest.fixture
def locked_carts(scratch: ScratchWarehouse) -> Iterator[None]:
    """Hold an ACCESS EXCLUSIVE lock on the scratch carts table (admin, test only)."""
    ready = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with psycopg.connect(scratch.admin_dsn) as conn:
            conn.execute(
                sql.SQL("LOCK TABLE {}.carts IN ACCESS EXCLUSIVE MODE").format(
                    sql.Identifier(scratch.schema)
                )
            )
            ready.set()
            release.wait(30)
            conn.rollback()

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert ready.wait(10)
    try:
        yield
    finally:
        release.set()
        thread.join(10)


def test_compiler_path_timeout_error(
    app: FastAPI,
    engine: Engine,
    db: Session,
    clock: FakeClock,
    scratch: ScratchWarehouse,
    warehouse_ro_dsn: str,
    request: pytest.FixtureRequest,
) -> None:
    quick = ReadOnlyExecutor(SecretStr(warehouse_ro_dsn), statement_timeout_seconds=0.5)
    world = scratch_world(app, engine, db, clock, scratch, warehouse_ro_dsn, executor=quick)
    world.llm.replies[:] = [interpretation(abandoned_carts(scratch.schema))]
    interpreted = world.client.post(
        INTERPRET, json={"nl_request": "Users who abandoned a cart"}
    ).json()
    request.getfixturevalue("locked_carts")

    response = world.client.post(PREVIEW, json=confirm(interpreted))

    assert response.status_code == 504
    assert error_code(response) == "query_timeout"
    assert "0.5s" in response.json()["error"]["message"]
