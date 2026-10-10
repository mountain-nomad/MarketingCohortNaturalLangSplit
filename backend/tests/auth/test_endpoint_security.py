"""Every API endpoint enforces authentication and its own permission (AC-A7, AC-A23, MVP AC-29).

The route inventory test forces every new ``/api`` endpoint to be classified here, so a
later feature cannot add an endpoint without negative tests.
"""

import re

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from cohortsplit.auth.catalog import GRANTABLE_PERMISSIONS
from cohortsplit.auth.models import Role, User
from tests.auth.helpers import (
    EXPORT_TEST_PATH,
    ApiClient,
    FakeClock,
    error_code,
    export_test_router,
    make_role,
    make_user,
)
from tests.semantic.routes import SEMANTIC_GATED

PUBLIC = {
    ("GET", "/api/health"),
    ("GET", "/api/ready"),
    ("POST", "/api/auth/login"),
}
AUTHENTICATED_ONLY = {
    ("GET", "/api/me"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/password"),
}
GATED: dict[tuple[str, str], str] = {
    ("GET", "/api/admin/users"): "user.read",
    ("POST", "/api/admin/users"): "user.create",
    ("GET", "/api/admin/users/{user_id}"): "user.read",
    ("PATCH", "/api/admin/users/{user_id}"): "user.update",
    ("PUT", "/api/admin/users/{user_id}/roles"): "role.assign",
    ("POST", "/api/admin/users/{user_id}/deactivate"): "user.deactivate",
    ("POST", "/api/admin/users/{user_id}/reactivate"): "user.deactivate",
    ("POST", "/api/admin/users/{user_id}/reset-password"): "user.reset_password",
    ("GET", "/api/admin/permissions"): "role.read",
    ("GET", "/api/admin/roles"): "role.read",
    ("POST", "/api/admin/roles"): "role.create",
    ("GET", "/api/admin/roles/{role_id}"): "role.read",
    ("PATCH", "/api/admin/roles/{role_id}"): "role.update",
    ("DELETE", "/api/admin/roles/{role_id}"): "role.delete",
    ("PUT", "/api/admin/roles/{role_id}/members"): "role.assign",
    ("GET", "/api/audit/events"): "audit.read",
}
GATED_IDS = [f"{m} {p}" for m, p in GATED]


def api_endpoints(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api")
        for method in route.methods - {"HEAD", "OPTIONS"}
    }


@pytest.fixture
def target_user(db: Session, clock: FakeClock) -> User:
    return make_user(db, "target@example.com", now=clock.now)


@pytest.fixture
def target_role(db: Session, clock: FakeClock) -> Role:
    return make_role(db, "Target role", ["cohort.create"], now=clock.now)


def concrete(path: str, user: User, role: Role) -> str:
    path = path.replace("{user_id}", str(user.id)).replace("{role_id}", str(role.id))
    assert not re.search(r"\{[^}]+\}", path), path
    return path


def client_with(app: FastAPI, db: Session, clock: FakeClock, permissions: set[str]) -> ApiClient:
    role = make_role(db, "Caller role", sorted(permissions), now=clock.now)
    make_user(db, "caller@example.com", now=clock.now, roles=[role])
    client = ApiClient(app)
    assert client.login("caller@example.com").status_code == 200
    return client


def test_route_inventory_is_fully_classified(app: FastAPI) -> None:
    # Semantic-context endpoints are classified and matrix-tested in tests/semantic.
    assert api_endpoints(app) == PUBLIC | AUTHENTICATED_ONLY | set(GATED) | set(SEMANTIC_GATED)


@pytest.mark.parametrize(("method", "path"), list(GATED), ids=GATED_IDS)
def test_every_gated_endpoint_refuses_unauthenticated(
    app: FastAPI, target_user: User, target_role: Role, method: str, path: str
) -> None:
    response = ApiClient(app).request(method, concrete(path, target_user, target_role), json={})

    assert response.status_code == 401
    assert error_code(response) == "not_authenticated"


@pytest.mark.parametrize(
    ("method", "path"),
    sorted(AUTHENTICATED_ONLY),
    ids=[f"{m} {p}" for m, p in sorted(AUTHENTICATED_ONLY)],
)
def test_authenticated_only_endpoints_refuse_unauthenticated(
    app: FastAPI, method: str, path: str
) -> None:
    assert ApiClient(app).request(method, path, json={}).status_code == 401


@pytest.mark.parametrize(("method", "path"), list(GATED), ids=GATED_IDS)
def test_every_gated_endpoint_refuses_missing_permission(
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    target_user: User,
    target_role: Role,
    method: str,
    path: str,
) -> None:
    required = GATED[(method, path)]
    client = client_with(app, db, clock, set(GRANTABLE_PERMISSIONS) - {required})

    response = client.request(method, concrete(path, target_user, target_role), json={})

    assert response.status_code == 403, response.text
    assert error_code(response) == "permission_denied"


@pytest.mark.parametrize(("method", "path"), list(GATED), ids=GATED_IDS)
def test_admin_passes_every_permission_check(
    admin_client: ApiClient, target_user: User, target_role: Role, method: str, path: str
) -> None:
    response = admin_client.request(method, concrete(path, target_user, target_role), json={})

    # 404 would mean the route is missing: every path here uses existing ids.
    assert response.status_code not in (401, 403, 404), response.text


@pytest.mark.parametrize(
    ("method", "path"),
    [k for k in GATED if k[0] != "GET"],
    ids=[f"{m} {p}" for m, p in GATED if m != "GET"],
)
def test_state_changing_admin_endpoints_require_csrf(
    admin_client: ApiClient, target_user: User, target_role: Role, method: str, path: str
) -> None:
    response = admin_client.http.request(method, concrete(path, target_user, target_role), json={})

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"


def test_cohort_create_only_user_refused_everywhere_else(
    app: FastAPI, db: Session, clock: FakeClock, target_user: User, target_role: Role
) -> None:
    app.include_router(export_test_router())
    client = client_with(app, db, clock, {"cohort.create"})

    for method, path in GATED:
        response = client.request(method, concrete(path, target_user, target_role), json={})
        assert response.status_code == 403, (method, path)
        assert error_code(response) == "permission_denied"

    export = client.post(EXPORT_TEST_PATH, json={"run_id": "r1", "columns": []})
    assert export.status_code == 403
    assert error_code(export) == "permission_denied"
    assert client.get("/api/me").json()["permissions"] == ["cohort.create"]
