"""POST /api/runs: deterministic split of a prepared cohort into a stored run
(FR-8, FR-9a, BR-5, AC-14 .. AC-18, AC-21c). Runs on SQLite and PostgreSQL."""

import dataclasses
import json
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from cohortsplit.experiment.assignment import Allocation
from cohortsplit.experiment.assignment import split as reference_split
from cohortsplit.experiment.models import ExperimentRun, ExperimentRunMember
from cohortsplit.warehouse.errors import (
    QueryTimeoutError,
    WarehouseQueryError,
    WarehouseUnavailableError,
)
from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.cohort.helpers import install_environment
from tests.experiment.helpers import (
    KEY,
    PHONE,
    RUNS,
    ExportExecutor,
    exporter,
    prepare,
    snapshot_of,
    split,
    split_body,
    split_ok,
)

MEMBERS = list(range(1, 41))


@pytest.fixture
def client(app: FastAPI, db: Session, clock: FakeClock) -> ApiClient:
    return exporter(app, db, clock)


def run_count(db: Session) -> int:
    db.expire_all()
    return int(db.scalar(select(func.count()).select_from(ExperimentRun)) or 0)


def expected_groups(members: list[int], test: int = 50) -> dict[str, str]:
    result = reference_split(KEY, members, Allocation(test=test, control=100 - test))
    return {str(u): "test" for u in result.test} | {str(u): "control" for u in result.control}


def test_split_returns_counts_and_stores_the_run(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))
    prepared = prepare(client)

    response = split(client, prepared)

    assert response.status_code == 201, response.text
    body = response.json()
    expected = expected_groups(MEMBERS)
    tests = sum(1 for g in expected.values() if g == "test")
    assert body["counts"] == {"test": tests, "control": 40 - tests, "total": 40}
    assert body["preview_count"] == 40
    assert body["experiment_key"] == KEY
    assert body["experiment_type"] == "ab"
    assert body["allocation"] == {"test": 50, "control": 50}
    assert body["assignment_version"] == 1
    assert body["warnings"] == []
    assert body["requires_empty_group_confirmation"] is False
    assert body["export_columns"] == []
    assert body["created_by"]["email"] == "marketer@example.com"
    assert body["download_count"] == 0
    assert len(body["id"]) == 32
    assert snapshot_of(db, body["id"]) == expected
    # The response never carries membership.
    assert "members" not in body
    assert run_count(db) == 1


def test_split_reexecutes_membership_through_the_executor(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(app, executor=ExportExecutor(members=[1, 2, 3]))
    prepared = prepare(client)
    preview_sql = env.executor.cohort_statements()[0].sql
    env.executor.members = [1, 2, 3, 4]  # the warehouse changed since the preview

    body = split(client, prepared).json()

    assert body["counts"]["total"] == 4
    assert body["preview_count"] == 3
    assert env.executor.cohort_statements()[-1].sql == preview_sql


def test_split_twice_gives_identical_assignment(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))

    first = split_ok(client)
    second = split_ok(client)

    assert first["id"] != second["id"]
    assert snapshot_of(db, first["id"]) == snapshot_of(db, second["id"])


def test_user_shared_by_two_cohorts_lands_in_same_group(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    env = install_environment(app, executor=ExportExecutor(members=list(range(1, 60))))
    first = split_ok(client)
    env.executor.members = list(range(30, 120))
    second = split_ok(client)

    a = snapshot_of(db, first["id"])
    b = snapshot_of(db, second["id"])
    shared = set(a) & set(b)
    assert len(shared) == 30
    assert all(a[user] == b[user] for user in shared)


def test_snapshot_groups_disjoint_union_equals_membership(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))

    body = split_ok(client, allocation={"test": 30, "control": 70})

    snapshot = snapshot_of(db, body["id"])
    assert set(snapshot) == {str(m) for m in MEMBERS}
    assert set(snapshot.values()) <= {"test", "control"}
    assert body["counts"]["test"] == sum(1 for g in snapshot.values() if g == "test")
    assert body["counts"]["control"] == sum(1 for g in snapshot.values() if g == "control")
    assert snapshot == expected_groups(MEMBERS, test=30)


def test_ac21c_snapshot_has_only_id_and_group(
    app: FastAPI, engine: Any, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    install_environment(
        app,
        executor=ExportExecutor(members=[1, 2], columns={PHONE: {1: "+4915111", 2: "+4915222"}}),
    )

    body = split_ok(client, export_columns=[PHONE])

    member_columns = {c["name"] for c in inspect(engine).get_columns("experiment_run_members")}
    assert member_columns == {"run_id", "position", "canonical_user_id", "assigned_group"}
    db.expire_all()
    run = db.get(ExperimentRun, body["id"])
    assert run is not None
    stored = json.dumps(
        {c.key: str(getattr(run, c.key)) for c in inspect(ExperimentRun).mapper.column_attrs}
    ) + json.dumps([str(m.canonical_user_id) for m in db.scalars(select(ExperimentRunMember))])
    assert "+4915" not in stored


def test_default_allocation_is_50_50(app: FastAPI, client: ApiClient) -> None:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))

    body = split(client, prepare(client)).json()

    assert body["allocation"] == {"test": 50, "control": 50}


def test_aa_split_uses_the_same_machinery(app: FastAPI, client: ApiClient, db: Session) -> None:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))

    ab = split_ok(client)
    aa = split_ok(client, experiment_type="aa")

    assert aa["experiment_type"] == "aa"
    assert snapshot_of(db, aa["id"]) == snapshot_of(db, ab["id"])


def test_key_is_trimmed(app: FastAPI, client: ApiClient) -> None:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))

    body = split(client, prepare(client), experiment_key=f"  {KEY}  ").json()

    assert body["experiment_key"] == KEY


INVALID: list[dict[str, Any]] = [
    {"allocation": {"test": 60, "control": 50}},
    {"allocation": {"test": 100, "control": 0}},
    {"allocation": {"test": 0, "control": 100}},
    {"allocation": {"test": 99.5, "control": 0.5}},
    {"experiment_key": ""},
    {"experiment_key": "   "},
    {"experiment_key": "../../etc/passwd"},
    {"experiment_key": "has space"},
    {"experiment_key": "x" * 101},
    {"experiment_type": "abc"},
    {"export_columns": ["phone"]},
    {"unexpected": 1},
]


@pytest.mark.parametrize("override", INVALID, ids=[json.dumps(i) for i in INVALID])
def test_invalid_split_input_refused(
    app: FastAPI, client: ApiClient, db: Session, override: dict[str, Any]
) -> None:
    env = install_environment(app, executor=ExportExecutor(members=MEMBERS))
    prepared = prepare(client)
    executed = len(env.executor.statements)

    response = client.post(RUNS, json={**split_body(prepared), **override})

    assert response.status_code == 422, response.text
    assert error_code(response) == "validation_error"
    assert len(env.executor.statements) == executed
    assert run_count(db) == 0


def test_missing_key_refused(app: FastAPI, client: ApiClient, db: Session) -> None:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))
    prepared = prepare(client)

    response = client.post(RUNS, json={"prepared_cohort_id": prepared})

    assert response.status_code == 422
    assert run_count(db) == 0


def test_reused_key_with_a_different_cohort_is_allowed(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(app, executor=ExportExecutor(members=MEMBERS))
    split_ok(client)
    env.executor.members = [100, 101, 102]

    assert split_ok(client)["counts"]["total"] == 3


def test_empty_cohort_at_split_is_refused(app: FastAPI, client: ApiClient, db: Session) -> None:
    env = install_environment(app, executor=ExportExecutor(members=MEMBERS))
    prepared = prepare(client)
    env.executor.members = []

    response = split(client, prepared)

    assert response.status_code == 422
    assert error_code(response) == "cohort_empty"
    assert run_count(db) == 0


def test_a_group_with_zero_users_warns_and_requires_confirmation(
    app: FastAPI, client: ApiClient
) -> None:
    # User 6 has bucket 374 for KEY (golden vector): test at 50/50, control is empty.
    install_environment(app, executor=ExportExecutor(members=[6]))

    body = split_ok(client)

    assert body["counts"] == {"test": 1, "control": 0, "total": 1}
    assert body["requires_empty_group_confirmation"] is True
    [warning] = body["warnings"]
    assert warning["code"] == "empty_group"
    assert warning["group"] == "control"
    assert "control" in warning["message"].lower()


def test_row_cap_exceeded_at_split_states_the_cap(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    env = install_environment(app, executor=ExportExecutor(members=[1, 2, 3]))
    prepared = prepare(client)
    env.executor.row_cap = 2

    response = split(client, prepared)

    assert response.status_code == 422
    assert error_code(response) == "row_cap_exceeded"
    assert response.json()["error"]["row_cap"] == 2
    assert run_count(db) == 0


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (QueryTimeoutError(30), 504, "query_timeout"),
        (WarehouseUnavailableError("warehouse down"), 503, "warehouse_unavailable"),
        (WarehouseQueryError("relation is gone"), 502, "warehouse_error"),
    ],
)
def test_warehouse_errors_at_split(
    app: FastAPI, client: ApiClient, db: Session, error: Exception, status: int, code: str
) -> None:
    env = install_environment(app, executor=ExportExecutor(members=MEMBERS))
    prepared = prepare(client)
    env.executor.error = error

    response = split(client, prepared)

    assert response.status_code == status
    assert error_code(response) == code
    assert run_count(db) == 0


def test_semantic_version_changed_since_preview_is_409(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    env = install_environment(app, executor=ExportExecutor(members=MEMBERS))
    prepared = prepare(client)
    env.snapshots[0] = dataclasses.replace(env.snapshot(), semantic_version="f" * 64)
    executed = len(env.executor.statements)

    response = split(client, prepared)

    assert response.status_code == 409
    assert error_code(response) == "semantic_version_changed"
    assert "interpret" in response.json()["error"]["message"].lower()
    assert len(env.executor.statements) == executed
    assert run_count(db) == 0


def test_unknown_prepared_cohort_is_not_found(app: FastAPI, client: ApiClient) -> None:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))

    response = split(client, "0" * 32)

    assert response.status_code == 404
    assert error_code(response) == "prepared_cohort_not_found"


def test_another_users_prepared_cohort_is_not_found(
    app: FastAPI, client: ApiClient, db: Session, clock: FakeClock
) -> None:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))
    prepared = prepare(client)
    other = exporter(app, db, clock, email="other@example.com")

    response = split(other, prepared)

    assert response.status_code == 404
    assert error_code(response) == "prepared_cohort_not_found"
    assert run_count(db) == 0


def test_expired_prepared_cohort_is_not_found(
    app: FastAPI, client: ApiClient, clock: FakeClock
) -> None:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))
    prepared = prepare(client)
    clock.advance(minutes=24 * 60 + 1)
    assert client.login("marketer@example.com").status_code == 200  # the session idled out

    response = split(client, prepared)

    assert response.status_code == 404
    assert error_code(response) == "prepared_cohort_not_found"


def test_split_export_columns_need_a_grant(app: FastAPI, client: ApiClient, db: Session) -> None:
    env = install_environment(app, executor=ExportExecutor(members=MEMBERS))
    prepared = prepare(client)
    executed = len(env.executor.statements)

    response = split(client, prepared, export_columns=[PHONE])

    assert response.status_code == 403
    assert error_code(response) == "export_column_denied"
    assert response.json()["error"]["columns"] == [PHONE]
    assert len(env.executor.statements) == executed
    assert run_count(db) == 0


def test_split_export_columns_without_cohort_export_are_denied(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock, columns=[PHONE], permissions=("cohort.create",))
    install_environment(app, executor=ExportExecutor(members=MEMBERS))

    response = split(client, prepare(client), export_columns=[PHONE])

    assert response.status_code == 403
    assert error_code(response) == "export_column_denied"


def test_granted_split_export_columns_are_stored(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    install_environment(app, executor=ExportExecutor(members=MEMBERS))

    body = split_ok(client, export_columns=[PHONE, PHONE, "public.users.user_id"])

    assert body["export_columns"] == [PHONE]


def test_split_export_column_outside_the_user_table_is_unsupported(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock, columns=["public.orders.status"])
    install_environment(app, executor=ExportExecutor(members=MEMBERS))

    response = split(client, prepare(client), export_columns=["public.orders.status"])

    assert response.status_code == 422
    assert error_code(response) == "export_column_unsupported"


def test_split_without_canonical_user_id_is_refused(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    env = install_environment(app, executor=ExportExecutor(members=MEMBERS))
    prepared = prepare(client)
    env.snapshots[0] = dataclasses.replace(env.snapshot(), canonical_user_id=None)

    response = split(client, prepared)

    assert response.status_code == 409
    assert run_count(db) == 0
