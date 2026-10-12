"""Run history: GET /api/runs, GET /api/runs/{id}; own runs only unless cohort.read_all;
someone else's run is 404 (FR-9a, FR-A6, AC-21d, ruling E4). Runs on SQLite and PostgreSQL."""

from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Session

from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.cohort.helpers import install_environment, marketer
from tests.experiment.helpers import RUNS, ExportExecutor, download, exporter, split_ok

MEMBERS = [1, 2, 3, 4, 5, 6]


def two_marketers(app: FastAPI, db: Session, clock: FakeClock) -> tuple[ApiClient, ApiClient]:
    install_environment(app, executor=ExportExecutor(members=MEMBERS))
    alice = exporter(app, db, clock, email="alice@example.com")
    bob = exporter(app, db, clock, email="bob@example.com")
    return alice, bob


def ids(body: dict[str, Any]) -> list[str]:
    return [item["id"] for item in body["items"]]


def test_list_own_runs_newest_first(app: FastAPI, db: Session, clock: FakeClock) -> None:
    alice, _ = two_marketers(app, db, clock)
    first = split_ok(alice)
    clock.advance(minutes=5)
    second = split_ok(alice, experiment_key="second_key", allocation={"test": 10, "control": 90})

    body = alice.get(RUNS).json()

    assert ids(body) == [second["id"], first["id"]]
    assert body["total"] == 2
    assert body["scope"] == "own"
    item = body["items"][0]
    assert item["experiment_key"] == "second_key"
    assert item["allocation"] == {"test": 10, "control": 90}
    assert item["counts"]["total"] == 6
    assert item["nl_request"]
    assert item["created_by"] == {
        "id": item["created_by"]["id"],
        "email": "alice@example.com",
        "display_name": "alice",
    }
    assert "members" not in item
    assert "sql" not in item


def test_list_is_paginated(app: FastAPI, db: Session, clock: FakeClock) -> None:
    alice, _ = two_marketers(app, db, clock)
    runs = []
    for minute in range(3):
        clock.advance(minutes=minute + 1)
        runs.append(split_ok(alice)["id"])

    page = alice.get(RUNS, params={"limit": 2, "offset": 1}).json()

    assert ids(page) == [runs[1], runs[0]]
    assert page["total"] == 3
    assert page["limit"] == 2
    assert page["offset"] == 1
    assert alice.get(RUNS, params={"limit": 0}).status_code == 422
    assert alice.get(RUNS, params={"limit": 201}).status_code == 422


def test_run_detail(app: FastAPI, db: Session, clock: FakeClock) -> None:
    alice, _ = two_marketers(app, db, clock)
    run = split_ok(alice)

    detail = alice.get(f"{RUNS}/{run['id']}").json()

    assert detail["id"] == run["id"]
    assert detail["counts"] == run["counts"]
    assert detail["spec"]["spec_version"] == "1"
    assert detail["sql"].startswith("SELECT DISTINCT")
    assert detail["sql_params"]
    assert detail["semantic_version"] == run["semantic_version"]
    assert detail["canonical_user_id"] == "public.users.user_id"
    assert detail["as_of"]
    assert "members" not in detail


def test_ac21d_cannot_list_get_or_download_others_runs(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: Any
) -> None:
    alice, bob = two_marketers(app, db, clock)
    bobs = split_ok(bob)
    alices = split_ok(alice)

    assert ids(alice.get(RUNS).json()) == [alices["id"]]
    detail = alice.get(f"{RUNS}/{bobs['id']}")
    assert detail.status_code == 404
    assert error_code(detail) == "run_not_found"
    fetched = download(alice, bobs["id"])
    assert fetched.status_code == 404
    assert error_code(fetched) == "run_not_found"
    # The same answer as for a run that does not exist (no existence leak).
    assert download(alice, "0" * 32).json() == fetched.json()
    assert not [e for e in audit_events.of("cohort.export") if e.target_id == bobs["id"]]


def test_read_all_sees_everyone(app: FastAPI, db: Session, clock: FakeClock) -> None:
    alice, bob = two_marketers(app, db, clock)
    a = split_ok(alice)
    clock.advance(minutes=1)
    b = split_ok(bob)
    auditor = marketer(app, db, clock, email="auditor@example.com", permissions=["cohort.read_all"])

    body = auditor.get(RUNS).json()

    assert ids(body) == [b["id"], a["id"]]
    assert body["scope"] == "all"
    assert auditor.get(f"{RUNS}/{a['id']}").status_code == 200
    # Seeing a run does not allow downloading it: that needs cohort.export.
    refused = download(auditor, a["id"])
    assert refused.status_code == 403
    assert error_code(refused) == "permission_denied"


def test_read_all_with_export_can_download_others_runs(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    alice, _ = two_marketers(app, db, clock)
    run = split_ok(alice)
    lead = marketer(
        app, db, clock, email="lead@example.com", permissions=["cohort.read_all", "cohort.export"]
    )

    response = download(lead, run["id"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


def test_admin_sees_every_run(
    app: FastAPI, db: Session, clock: FakeClock, admin_client: ApiClient
) -> None:
    alice, bob = two_marketers(app, db, clock)
    split_ok(alice)
    split_ok(bob)

    assert admin_client.get(RUNS).json()["total"] == 2


def test_history_needs_create_or_read_all(app: FastAPI, db: Session, clock: FakeClock) -> None:
    alice, _ = two_marketers(app, db, clock)
    run = split_ok(alice)
    exporter_only = marketer(app, db, clock, email="x@example.com", permissions=["cohort.export"])

    assert exporter_only.get(RUNS).status_code == 403
    assert exporter_only.get(f"{RUNS}/{run['id']}").status_code == 403


def test_owner_keeps_access_after_losing_create_but_needs_a_history_permission(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    from sqlalchemy import delete

    from cohortsplit.auth.models import RolePermission

    alice, _ = two_marketers(app, db, clock)
    run = split_ok(alice)
    db.execute(delete(RolePermission).where(RolePermission.permission_key == "cohort.create"))
    db.commit()

    assert alice.get(RUNS).status_code == 403
    assert download(alice, run["id"]).status_code == 200


def test_malformed_run_id_is_not_found(app: FastAPI, db: Session, clock: FakeClock) -> None:
    alice, _ = two_marketers(app, db, clock)

    for bad in ("not-a-run", "0" * 31, "Z" * 32):
        response = alice.get(f"{RUNS}/{bad}")
        assert response.status_code == 404
        assert error_code(response) == "run_not_found"
