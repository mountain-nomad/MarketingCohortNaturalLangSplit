"""POST /api/cohorts/preview: confirmed spec hash + semantic version, deterministic SQL,
validated, executed through the executor; count, samples, SQL, NULL counts (FR-5..FR-7)."""

import dataclasses
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cohortsplit.cohort.models import PreparedCohortRow
from cohortsplit.cohort.store import PreparedCohortStore, execute_membership
from cohortsplit.compiler.validator import validate_select
from cohortsplit.warehouse.errors import QueryTimeoutError, WarehouseUnavailableError
from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.cohort.helpers import (
    AC2_READABLE,
    PREVIEW,
    FakeExecutor,
    confirm,
    install_environment,
    interpret_ok,
    marketer,
)
from tests.llm_fakes import interpretation
from tests.unit.compiler.fixtures import ORDER_TO_CATEGORY, shop_snapshot

PHONE = "public.users.phone"
FIRST_NAME = "public.users.first_name"


@pytest.fixture
def client(app: FastAPI, db: Session, clock: FakeClock) -> ApiClient:
    return marketer(app, db, clock)


def exporter(app: FastAPI, db: Session, clock: FakeClock, *columns: str) -> ApiClient:
    return marketer(
        app,
        db,
        clock,
        email="exporter@example.com",
        permissions=("cohort.create", "cohort.export"),
        export_columns=columns,
    )


def preview(client: ApiClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(PREVIEW, json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def prepared_rows(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(PreparedCohortRow)) or 0)


def test_preview_returns_count_samples_sql_and_prepared_cohort(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    env = install_environment(app)
    interpreted = interpret_ok(client)

    body = preview(client, confirm(interpreted))

    assert body["total_count"] == 3
    assert body["empty"] is False
    assert body["split_export_enabled"] is True
    assert body["disabled_reason"] is None
    assert body["sample"]["columns"] == ["canonical_user_id"]
    assert body["sample"]["rows"] == [[1], [2], [3]]
    assert body["null_counts"] == {}
    assert body["readable"] == AC2_READABLE
    assert body["spec_hash"] == interpreted["spec_hash"]
    assert body["semantic_version"] == interpreted["semantic_version"]
    assert body["row_cap"] == env.executor.row_cap
    assert body["sql"].startswith('SELECT DISTINCT u."user_id" AS "canonical_user_id"')
    assert len(body["sql_hash"]) == 64
    assert body["sql_params"] == {
        "p1": "KZ",
        "p2": "abandoned",
        "p3": "paid",
        "p4": "shipped",
        "p5": "delivered",
    }
    assert body["prepared_cohort_id"]
    # The membership query is exactly the SQL shown, executed with the executor's cap.
    membership = env.executor.cohort_statements()[0]
    assert membership.sql == body["sql"]
    assert membership.row_cap is None
    validate_select(body["sql"], allowed_tables=env.executor.allowed_tables)
    assert prepared_rows(db) == 1


def test_preview_sql_identical_across_runs(app: FastAPI, client: ApiClient) -> None:
    install_environment(app)

    first = preview(client, confirm(interpret_ok(client)))
    second = preview(client, confirm(interpret_ok(client)))

    assert first["sql"] == second["sql"]
    assert first["sql_hash"] == second["sql_hash"]
    assert first["spec_hash"] == second["spec_hash"]


def test_granted_export_columns_in_samples_with_null_counts(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    install_environment(
        app,
        executor=FakeExecutor(
            members=[1, 2, 3],
            columns={
                PHONE: {1: "+491", 2: None, 3: None},
                FIRST_NAME: {1: "Ann", 2: "Bob", 3: "C"},
            },
        ),
    )
    client = exporter(app, db, clock, PHONE, FIRST_NAME)

    body = preview(client, confirm(interpret_ok(client), export_columns=[PHONE]))

    assert body["sample"]["columns"] == ["canonical_user_id", PHONE]
    assert body["sample"]["rows"] == [[1, "+491"], [2, None], [3, None]]
    assert body["null_counts"] == {PHONE: 2}


def test_export_column_not_granted_is_refused_and_nothing_executed(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: Any
) -> None:
    env = install_environment(app)
    client = exporter(app, db, clock, FIRST_NAME)

    response = client.post(PREVIEW, json=confirm(interpret_ok(client), export_columns=[PHONE]))

    assert response.status_code == 403
    assert error_code(response) == "export_column_denied"
    assert env.executor.statements == []
    [denied] = [e for e in audit_events.all() if e.action == "cohort.run"]
    assert denied.outcome == "denied"
    assert denied.metadata_["denied_columns"] == [PHONE]


def test_export_columns_need_cohort_export(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(app)

    response = client.post(PREVIEW, json=confirm(interpret_ok(client), export_columns=[PHONE]))

    assert response.status_code == 403
    assert error_code(response) == "export_column_denied"
    assert env.executor.statements == []


def test_granted_column_outside_the_user_table_is_unsupported(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    env = install_environment(app)
    client = exporter(app, db, clock, "public.orders.status")

    response = client.post(
        PREVIEW, json=confirm(interpret_ok(client), export_columns=["public.orders.status"])
    )

    assert response.status_code == 422
    assert error_code(response) == "export_column_unsupported"
    assert env.executor.statements == []


def test_semantic_version_changed_since_interpretation_is_409(
    app: FastAPI, client: ApiClient
) -> None:
    env = install_environment(app)
    interpreted = interpret_ok(client)
    changed = dataclasses.replace(env.snapshot(), semantic_version="f" * 64)
    env.snapshots[0] = changed

    response = client.post(PREVIEW, json=confirm(interpreted))

    assert response.status_code == 409
    assert error_code(response) == "semantic_version_changed"
    assert "interpret" in response.json()["error"]["message"].lower()
    assert env.executor.statements == []


@pytest.mark.parametrize("field", ["spec_hash", "semantic_version"])
def test_confirmation_must_match_the_interpretation(
    app: FastAPI, client: ApiClient, field: str
) -> None:
    env = install_environment(app)
    body = confirm(interpret_ok(client))
    body[field] = "0" * 64

    response = client.post(PREVIEW, json=body)

    assert response.status_code == 409
    assert error_code(response) == "confirmation_mismatch"
    assert env.executor.statements == []


def test_another_users_interpretation_is_not_found(
    app: FastAPI, client: ApiClient, db: Session, clock: FakeClock
) -> None:
    env = install_environment(app)
    interpreted = interpret_ok(client)
    other = marketer(app, db, clock, email="other@example.com")

    response = other.post(PREVIEW, json=confirm(interpreted))

    assert response.status_code == 404
    assert error_code(response) == "interpretation_not_found"
    assert env.executor.statements == []


def test_expired_interpretation_is_not_found(
    app: FastAPI, client: ApiClient, clock: FakeClock
) -> None:
    env = install_environment(app)
    interpreted = interpret_ok(client)
    clock.advance(minutes=61)

    response = client.post(PREVIEW, json=confirm(interpreted))

    assert response.status_code == 404
    assert error_code(response) == "interpretation_not_found"
    assert env.executor.statements == []


def test_empty_cohort_disables_split_and_export(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    install_environment(app, executor=FakeExecutor(members=[]))

    body = preview(client, confirm(interpret_ok(client)))

    assert body["total_count"] == 0
    assert body["empty"] is True
    assert body["split_export_enabled"] is False
    assert "empty" in body["disabled_reason"]
    assert body["sample"]["rows"] == []
    assert body["prepared_cohort_id"] is None
    assert prepared_rows(db) == 0


def test_row_cap_refusal_states_cap(app: FastAPI, client: ApiClient, db: Session) -> None:
    install_environment(app, executor=FakeExecutor(members=[1, 2, 3, 4], row_cap=3))

    response = client.post(PREVIEW, json=confirm(interpret_ok(client)))

    assert response.status_code == 422
    assert error_code(response) == "row_cap_exceeded"
    assert "3" in response.json()["error"]["message"]
    assert response.json()["error"]["row_cap"] == 3
    assert prepared_rows(db) == 0


def test_query_timeout_names_the_timeout(app: FastAPI, client: ApiClient) -> None:
    install_environment(app, executor=FakeExecutor(error=QueryTimeoutError(30)))

    response = client.post(PREVIEW, json=confirm(interpret_ok(client)))

    assert response.status_code == 504
    assert error_code(response) == "query_timeout"
    assert "30s" in response.json()["error"]["message"]


def test_warehouse_unavailable(app: FastAPI, client: ApiClient) -> None:
    install_environment(
        app, executor=FakeExecutor(error=WarehouseUnavailableError("Warehouse is unreachable"))
    )

    response = client.post(PREVIEW, json=confirm(interpret_ok(client)))

    assert response.status_code == 503
    assert error_code(response) == "warehouse_unavailable"


def test_unknown_lookup_value_refused_before_the_cohort_query(
    app: FastAPI, client: ApiClient
) -> None:
    spec = {
        "spec_version": "1",
        "where": {
            "type": "metric",
            "term": "purchase",
            "path": ORDER_TO_CATEGORY[:4],
            "filters": [
                {"column": "public.products.name", "operator": "=", "value": "Unicorn Tea"}
            ],
        },
    }
    env = install_environment(
        app,
        replies=[interpretation(spec)],
        executor=FakeExecutor(existing={"public.products.name": {"Chai"}}),
    )
    interpreted = interpret_ok(client, "Users who bought Unicorn Tea")

    response = client.post(PREVIEW, json=confirm(interpreted))

    assert response.status_code == 422
    assert error_code(response) == "unknown_value"
    assert "Unicorn Tea" in response.json()["error"]["message"]
    assert "public.products.name" in response.json()["error"]["message"]
    assert env.executor.cohort_statements() == []


def test_known_lookup_value_proceeds(app: FastAPI, client: ApiClient) -> None:
    spec = {
        "spec_version": "1",
        "where": {
            "type": "metric",
            "term": "purchase",
            "path": ORDER_TO_CATEGORY[:4],
            "filters": [{"column": "public.products.name", "operator": "=", "value": "Chai"}],
        },
    }
    env = install_environment(
        app,
        replies=[interpretation(spec)],
        executor=FakeExecutor(existing={"public.products.name": {"Chai"}}),
    )

    body = preview(client, confirm(interpret_ok(client, "Users who bought Chai")))

    assert body["total_count"] == 3
    assert env.executor.statements[0].sql.startswith('SELECT DISTINCT t."name"')


def test_prepared_cohort_stores_definition_not_rows(
    app: FastAPI, client: ApiClient, db: Session, clock: FakeClock
) -> None:
    install_environment(
        app,
        executor=FakeExecutor(members=[1, 2, 3], columns={PHONE: {1: "+491", 2: None, 3: "+3"}}),
    )
    interpreted = interpret_ok(client)
    body = preview(client, confirm(interpreted))

    store = PreparedCohortStore()
    owner_id = client.get("/api/me").json()["id"]
    prepared = store.get(db, body["prepared_cohort_id"], user_id=owner_id, now=clock.now)

    assert prepared is not None
    assert prepared.nl_request.startswith("Users from Kazakhstan")
    assert prepared.spec_hash == interpreted["spec_hash"]
    assert prepared.sql == body["sql"]
    assert prepared.sql_hash == body["sql_hash"]
    assert prepared.semantic_version == interpreted["semantic_version"]
    assert prepared.row_count == 3
    assert prepared.dialect == "postgresql"
    assert prepared.as_of == clock.now
    row = db.get(PreparedCohortRow, body["prepared_cohort_id"])
    assert row is not None
    stored = str({c.name: getattr(row, c.key) for c in PreparedCohortRow.__table__.columns})
    assert "+491" not in stored
    assert store.get(db, body["prepared_cohort_id"], user_id=owner_id + 999, now=clock.now) is None
    clock.advance(hours=25)
    assert store.get(db, body["prepared_cohort_id"], user_id=owner_id, now=clock.now) is None


def test_execute_membership_reexecutes_the_stored_sql(
    app: FastAPI, client: ApiClient, db: Session, clock: FakeClock
) -> None:
    env = install_environment(app)
    body = preview(client, confirm(interpret_ok(client)))
    owner_id = client.get("/api/me").json()["id"]
    prepared = PreparedCohortStore().get(
        db, body["prepared_cohort_id"], user_id=owner_id, now=clock.now
    )
    assert prepared is not None
    before = len(env.executor.statements)

    members = execute_membership(env.executor, prepared, allowed_tables=env.executor.allowed_tables)

    assert members == (1, 2, 3)
    [statement] = env.executor.statements[before:]
    assert statement.sql == prepared.sql
    assert statement.params == prepared.params


def test_execute_membership_refuses_tampered_sql(
    app: FastAPI, client: ApiClient, db: Session, clock: FakeClock
) -> None:
    from cohortsplit.compiler.validator import SqlValidationError

    env = install_environment(app)
    body = preview(client, confirm(interpret_ok(client)))
    owner_id = client.get("/api/me").json()["id"]
    prepared = PreparedCohortStore().get(
        db, body["prepared_cohort_id"], user_id=owner_id, now=clock.now
    )
    assert prepared is not None
    tampered = dataclasses.replace(prepared, sql="DELETE FROM public.users")

    with pytest.raises(SqlValidationError):
        execute_membership(env.executor, tampered, allowed_tables=env.executor.allowed_tables)


def test_snapshot_without_tables_refuses_preview(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(app)
    interpreted = interpret_ok(client)
    env.snapshots[0] = shop_snapshot(docs=[])

    response = client.post(PREVIEW, json=confirm(interpreted))

    assert response.status_code == 409
    assert env.executor.statements == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"interpretation_id": "x", "spec_hash": "a" * 64, "semantic_version": "b" * 64},
        {"interpretation_id": "a" * 32, "spec_hash": "short", "semantic_version": "b" * 64},
        {
            "interpretation_id": "a" * 32,
            "spec_hash": "a" * 64,
            "semantic_version": "b" * 64,
            "export_columns": ["phone"],
        },
        {
            "interpretation_id": "a" * 32,
            "spec_hash": "a" * 64,
            "semantic_version": "b" * 64,
            "sql": "SELECT 1",
        },
    ],
    ids=["empty", "bad-id", "bad-hash", "unqualified-column", "extra-sql-field"],
)
def test_preview_body_is_validated(app: FastAPI, client: ApiClient, body: dict[str, Any]) -> None:
    env = install_environment(app)

    response = client.post(PREVIEW, json=body)

    assert response.status_code == 422
    assert env.executor.statements == []
