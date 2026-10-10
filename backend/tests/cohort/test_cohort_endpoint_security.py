"""Every cohort endpoint refuses unauthenticated callers and callers without cohort.create
(AC-29, AC-A7, FR-A6). Runs on SQLite and PostgreSQL."""

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from cohortsplit.auth.catalog import GRANTABLE_PERMISSIONS
from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.cohort.helpers import INTERPRET, PREVIEW, install_environment, marketer
from tests.cohort.routes import COHORT_GATED

IDS = [f"{m} {p}" for m, p in COHORT_GATED]


@pytest.mark.parametrize(("method", "path"), list(COHORT_GATED), ids=IDS)
def test_every_cohort_endpoint_refuses_unauthenticated(
    app: FastAPI, method: str, path: str
) -> None:
    env = install_environment(app)

    response = ApiClient(app).request(method, path, json={"nl_request": "Users from DE"})

    assert response.status_code == 401
    assert error_code(response) == "not_authenticated"
    assert env.llm.calls == []
    assert env.executor.statements == []


@pytest.mark.parametrize(("method", "path"), list(COHORT_GATED), ids=IDS)
def test_every_cohort_endpoint_refuses_missing_permission(
    app: FastAPI, db: Session, clock: FakeClock, method: str, path: str
) -> None:
    env = install_environment(app)
    required = COHORT_GATED[(method, path)]
    client = marketer(app, db, clock, permissions=set(GRANTABLE_PERMISSIONS) - {required})

    response = client.request(method, path, json={"nl_request": "Users from DE"})

    assert response.status_code == 403, response.text
    assert error_code(response) == "permission_denied"
    assert env.llm.calls == []
    assert env.executor.statements == []


@pytest.mark.parametrize(("method", "path"), list(COHORT_GATED), ids=IDS)
def test_cohort_endpoints_require_csrf(admin_client: ApiClient, method: str, path: str) -> None:
    response = admin_client.http.request(method, path, json={"nl_request": "Users from DE"})

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"


def test_cohort_create_reaches_both_endpoints(app: FastAPI, db: Session, clock: FakeClock) -> None:
    install_environment(app)
    client = marketer(app, db, clock)

    interpreted = client.post(INTERPRET, json={"nl_request": "Users from DE"})
    assert interpreted.status_code == 200, interpreted.text
    previewed = client.post(PREVIEW, json={})
    assert previewed.status_code == 422  # validation, i.e. past the permission check


def test_permission_removed_takes_effect_on_next_request(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    from sqlalchemy import delete

    from cohortsplit.auth.models import RolePermission

    install_environment(app)
    client = marketer(app, db, clock)
    assert client.post(INTERPRET, json={"nl_request": "Users from DE"}).status_code == 200

    db.execute(delete(RolePermission).where(RolePermission.permission_key == "cohort.create"))
    db.commit()

    response = client.post(INTERPRET, json={"nl_request": "Users from DE"})
    assert response.status_code == 403
