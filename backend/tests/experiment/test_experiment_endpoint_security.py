"""Every experiment endpoint refuses unauthenticated callers, callers without any of its
permissions, and unsafe requests without CSRF (AC-29, AC-A7, FR-A6). SQLite and PostgreSQL."""

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from cohortsplit.auth.catalog import GRANTABLE_PERMISSIONS
from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.cohort.helpers import install_environment, marketer
from tests.experiment.helpers import ExportExecutor, exporter, split_ok
from tests.experiment.routes import EXPERIMENT_GATED

IDS = [f"{m} {p}" for m, p in EXPERIMENT_GATED]
UNSAFE = [k for k in EXPERIMENT_GATED if k[0] != "GET"]
BODY = {"prepared_cohort_id": "0" * 32, "experiment_key": "k"}


def concrete(path: str, run_id: str = "0" * 32) -> str:
    return path.replace("{run_id}", run_id)


@pytest.mark.parametrize(("method", "path"), list(EXPERIMENT_GATED), ids=IDS)
def test_every_experiment_endpoint_refuses_unauthenticated(
    app: FastAPI, method: str, path: str
) -> None:
    env = install_environment(app, executor=ExportExecutor())

    response = ApiClient(app).request(method, concrete(path), json=BODY)

    assert response.status_code == 401
    assert error_code(response) == "not_authenticated"
    assert env.executor.statements == []


@pytest.mark.parametrize(("method", "path"), list(EXPERIMENT_GATED), ids=IDS)
def test_every_experiment_endpoint_refuses_missing_permission(
    app: FastAPI, db: Session, clock: FakeClock, method: str, path: str
) -> None:
    env = install_environment(app, executor=ExportExecutor())
    owner = exporter(app, db, clock, email="owner@example.com")
    run = split_ok(owner)
    executed = len(env.executor.statements)
    admits = set(EXPERIMENT_GATED[(method, path)])
    client = marketer(
        app, db, clock, email="caller@example.com", permissions=GRANTABLE_PERMISSIONS - admits
    )

    response = client.request(method, concrete(path, run["id"]), json=BODY)

    assert response.status_code == 403, response.text
    assert error_code(response) == "permission_denied"
    assert len(env.executor.statements) == executed


@pytest.mark.parametrize(("method", "path"), UNSAFE, ids=[f"{m} {p}" for m, p in UNSAFE])
def test_unsafe_experiment_endpoints_require_csrf(
    admin_client: ApiClient, method: str, path: str
) -> None:
    response = admin_client.http.request(method, concrete(path), json=BODY)

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"


@pytest.mark.parametrize(
    ("method", "path"),
    [k for k in EXPERIMENT_GATED if len(EXPERIMENT_GATED[k]) > 1],
    ids=[f"{m} {p}" for m, p in EXPERIMENT_GATED if len(EXPERIMENT_GATED[(m, p)]) > 1],
)
def test_each_alternative_permission_admits(
    app: FastAPI, db: Session, clock: FakeClock, method: str, path: str
) -> None:
    install_environment(app, executor=ExportExecutor())
    owner = exporter(app, db, clock, email="owner@example.com")
    run = split_ok(owner)
    for index, permission in enumerate(EXPERIMENT_GATED[(method, path)]):
        client = marketer(app, db, clock, email=f"p{index}@example.com", permissions=[permission])
        response = client.request(method, concrete(path, run["id"]))
        assert response.status_code in (200, 404), (permission, response.text)


def test_admin_passes_every_permission_check(
    app: FastAPI, db: Session, clock: FakeClock, admin_client: ApiClient
) -> None:
    install_environment(app, executor=ExportExecutor())
    run = split_ok(exporter(app, db, clock, email="owner@example.com"))

    assert admin_client.get("/api/runs").status_code == 200
    assert admin_client.get(f"/api/runs/{run['id']}").status_code == 200
    downloaded = admin_client.post(f"/api/runs/{run['id']}/download", json={})
    assert downloaded.status_code == 200
    split = admin_client.post("/api/runs", json=BODY)
    assert split.status_code == 404  # unknown prepared cohort: past the permission check
