"""Split, export and re-download end to end on the demo warehouse (integration).

Real appdb, crawl, semantic provider and read-only executor; only the LLM is a fake that
returns the spec a correct interpreter would produce (FR-8, FR-9, FR-9a, AC-14, AC-16,
AC-20, AC-21, AC-21a, AC-21b; missing users on a scratch schema).
"""

import csv
import io
import json
import zipfile
from collections.abc import Sequence
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from cohortsplit.app import create_app
from cohortsplit.auth.clock import get_clock
from cohortsplit.auth.models import RoleExportColumn
from cohortsplit.cohort.environment import get_cohort_environment
from cohortsplit.config import Settings
from cohortsplit.experiment.assignment import Allocation, assign
from tests.auth.helpers import ApiClient, FakeClock, error_code, make_role, make_user
from tests.cohort.helpers import INTERPRET, PREVIEW, confirm
from tests.cohort_reference.test_cohort_reference import ac2_spec
from tests.cohort_reference.world import World, build_world, reference
from tests.integration.conftest import ScratchWarehouse
from tests.llm_fakes import interpretation

pytestmark = pytest.mark.integration

S = "public"
PHONE = f"{S}.users.phone"
KEY = "push_demo_2026"
VARIANT_SQL = """
    SELECT u.user_id FROM public.users u
    WHERE EXISTS (SELECT 1 FROM public.addresses a
                  WHERE a.user_id = u.user_id AND a.country_code = 'DE')
      AND EXISTS (SELECT 1 FROM public.carts c
                  WHERE c.user_id = u.user_id AND c.status = 'active')
      AND NOT EXISTS (SELECT 1 FROM public.orders o
                      WHERE o.user_id = u.user_id AND o.discount_total > 0)
"""
PAID_SQL = """
    SELECT u.user_id FROM public.users u
    WHERE EXISTS (SELECT 1 FROM public.orders o
                  WHERE o.user_id = u.user_id AND o.status IN ('paid', 'shipped', 'delivered'))
"""
PAID_SPEC: dict[str, Any] = {
    "spec_version": "1",
    "where": {"type": "metric", "term": "paid_order"},
}


def campaign_marketer(
    app: FastAPI, db: Session, clock: FakeClock, columns: list[str], email: str = "mk@example.com"
) -> ApiClient:
    role = make_role(
        db, f"Campaign {email}", ["cohort.create", "cohort.export"], columns, now=clock.now
    )
    make_user(db, email, now=clock.now, roles=[role])
    client = ApiClient(app)
    assert client.login(email).status_code == 200
    return client


def run_split(
    world: World,
    client: ApiClient,
    request: str,
    spec: dict[str, Any],
    *,
    columns: Sequence[str] = (),
    key: str = KEY,
) -> dict[str, Any]:
    world.llm.replies[:] = [interpretation(spec)]
    interpreted = client.post(INTERPRET, json={"nl_request": request})
    assert interpreted.status_code == 200, interpreted.text
    assert interpreted.json()["kind"] == "interpretation", interpreted.json()
    previewed = client.post(PREVIEW, json=confirm(interpreted.json(), export_columns=list(columns)))
    assert previewed.status_code == 200, previewed.text
    prepared = previewed.json()["prepared_cohort_id"]
    response = client.post(
        "/api/runs",
        json={
            "prepared_cohort_id": prepared,
            "experiment_key": key,
            "export_columns": list(columns),
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def unzip(response: Any, key: str = KEY) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == [f"{key}_test.csv", f"{key}_control.csv", "manifest.json"]
        return {
            "test": list(csv.reader(io.StringIO(archive.read(f"{key}_test.csv").decode()))),
            "control": list(csv.reader(io.StringIO(archive.read(f"{key}_control.csv").decode()))),
            "manifest": json.loads(archive.read("manifest.json")),
        }


def phones(dsn: str, ids: set[int]) -> dict[str, str]:
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        rows = conn.execute(
            "SELECT user_id, phone FROM public.users WHERE user_id = ANY(%s)", (list(ids),)
        ).fetchall()
    return {str(user): phone or "" for user, phone in rows}


@pytest.fixture
def world(
    app: FastAPI, engine: Engine, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> World:
    return build_world(app, engine, db, clock, ro_dsn=warehouse_ro_dsn, schema=S)


def test_full_campaign_split_download_redownload(
    world: World, app: FastAPI, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> None:
    expected = {int(u) for u in reference(warehouse_ro_dsn, PAID_SQL)}
    assert len(expected) > 10
    client = campaign_marketer(app, db, clock, [PHONE])

    run = run_split(world, client, "Users who paid for an order", PAID_SPEC, columns=[PHONE])
    first = unzip(client.post(f"/api/runs/{run['id']}/download", json={}))

    assert first["test"][0] == ["user_id", "phone"]
    test_ids = {int(r[0]) for r in first["test"][1:]}
    control_ids = {int(r[0]) for r in first["control"][1:]}
    assert not test_ids & control_ids  # AC-16
    assert test_ids | control_ids == expected
    assert len(first["test"]) - 1 + len(first["control"]) - 1 == len(expected)  # one row each
    half = Allocation(test=50, control=50)
    assert all(assign(KEY, u, half) == "test" for u in test_ids)
    assert all(assign(KEY, u, half) == "control" for u in control_ids)
    real = phones(warehouse_ro_dsn, expected)
    for row in first["test"][1:] + first["control"][1:]:
        assert row[1].removeprefix("'") == real[row[0]]
    manifest = first["manifest"]
    assert manifest["nl_request"] == "Users who paid for an order"
    assert manifest["counts"] == {
        "test": len(test_ids),
        "control": len(control_ids),
        "total": len(expected),
    }
    assert manifest["experiment"]["key"] == KEY
    assert manifest["semantic_version"] == world.snapshot().semantic_version
    assert manifest["export"]["missing_from_warehouse"] == {"test": 0, "control": 0}

    again = unzip(client.post(f"/api/runs/{run['id']}/download", json={}))
    assert again["test"] == first["test"]
    assert again["control"] == first["control"]
    assert again["manifest"]["export"]["redownload"] is True

    db.execute(delete(RoleExportColumn).where(RoleExportColumn.column_ref == PHONE))
    db.commit()
    refused = client.post(f"/api/runs/{run['id']}/download", json={})
    assert refused.status_code == 403
    assert error_code(refused) == "export_column_denied"
    ids_only = unzip(client.post(f"/api/runs/{run['id']}/download", json={"columns": []}))
    assert [r[0] for r in ids_only["test"]] == [r[0] for r in first["test"]]


def test_ac2_shaped_variant_split(
    world: World, app: FastAPI, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> None:
    expected = {str(u) for u in reference(warehouse_ro_dsn, VARIANT_SQL)}
    client = campaign_marketer(app, db, clock, [])

    run = run_split(
        world,
        client,
        "Users from Germany with an active cart who never used a discount",
        {"spec_version": "1", "where": ac2_spec("active", "discounted_order")},
    )
    export = unzip(
        client.post(f"/api/runs/{run['id']}/download", json={"confirm_empty_group": True})
    )

    got = {r[0] for r in export["test"][1:]} | {r[0] for r in export["control"][1:]}
    assert got == expected
    assert run["counts"]["total"] == len(expected)


def test_split_identical_across_app_restart(
    world: World,
    app: FastAPI,
    engine: Engine,
    settings: Settings,
    db: Session,
    clock: FakeClock,
) -> None:
    client = campaign_marketer(app, db, clock, [])
    before = run_split(world, client, "Users who paid for an order", PAID_SPEC)
    original = unzip(client.post(f"/api/runs/{before['id']}/download", json={}))

    restarted = create_app(settings, engine=engine)
    restarted.dependency_overrides[get_clock] = lambda: clock
    restarted.dependency_overrides[get_cohort_environment] = app.dependency_overrides[
        get_cohort_environment
    ]
    fresh = ApiClient(restarted)
    assert fresh.login("mk@example.com").status_code == 200
    after = run_split(world, fresh, "Users who paid for an order", PAID_SPEC)
    repeated = unzip(fresh.post(f"/api/runs/{after['id']}/download", json={}))

    assert after["id"] != before["id"]
    assert repeated["test"] == original["test"]
    assert repeated["control"] == original["control"]


def test_users_gone_from_the_warehouse_keep_their_row(
    app: FastAPI,
    engine: Engine,
    db: Session,
    clock: FakeClock,
    scratch: ScratchWarehouse,
    warehouse_ro_dsn: str,
) -> None:
    schema = scratch.schema
    email = f"{schema}.users.email"
    world = build_world(
        app,
        engine,
        db,
        clock,
        ro_dsn=warehouse_ro_dsn,
        schema=schema,
        entries=[
            {
                "key": "customer_id",
                "definition": {"kind": "canonical_user_id", "column": f"{schema}.users.user_id"},
            }
        ],
    )
    client = campaign_marketer(app, db, clock, [email])
    run = run_split(world, client, "All users", {"spec_version": "1"}, columns=[email])
    assert run["counts"]["total"] == 10

    scratch.admin("DELETE FROM {s}.orders WHERE user_id = 3")
    scratch.admin("DELETE FROM {s}.carts WHERE user_id = 3")
    scratch.admin("DELETE FROM {s}.users WHERE user_id = 3")
    export = unzip(client.post(f"/api/runs/{run['id']}/download", json={}))

    rows = {r[0]: r[1] for r in export["test"][1:] + export["control"][1:]}
    assert len(rows) == 10
    assert rows["3"] == ""
    assert rows["4"] == "scratch4@example.com"
    group = assign(KEY, 3, Allocation(test=50, control=50))
    missing = export["manifest"]["export"]["missing_from_warehouse"]
    assert missing[group] == 1
    assert sum(missing.values()) == 1
