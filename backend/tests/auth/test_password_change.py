"""Password change, forced change after a temporary password (FR-A1, FR-A2, AC-A4, AC-A6)."""

import re

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from cohortsplit.auth import passwords
from cohortsplit.auth.models import User
from tests.auth.conftest import AuditReader
from tests.auth.helpers import (
    OTHER_PASSWORD,
    STRONG_PASSWORD,
    THIRD_PASSWORD,
    ApiClient,
    FakeClock,
    admin_role,
    error_code,
    make_user,
)

PUBLIC_ENDPOINTS = {
    ("GET", "/api/health"),
    ("GET", "/api/ready"),
    ("POST", "/api/auth/login"),
}
ALLOWED_DURING_FORCED_CHANGE = {
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/password"),
}


def api_endpoints(app: FastAPI) -> list[tuple[str, str]]:
    endpoints = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api"):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                endpoints.append((method, route.path))
    return endpoints


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


@pytest.fixture
def temp_user(db: Session, clock: FakeClock) -> User:
    # An admin with a temporary password: every permission, yet still gated.
    return make_user(
        db,
        "temp@example.com",
        now=clock.now,
        password=STRONG_PASSWORD,
        roles=[admin_role(db)],
        must_change_password=True,
    )


@pytest.fixture
def temp_client(app: FastAPI, temp_user: User) -> ApiClient:
    client = ApiClient(app)
    response = client.login("temp@example.com")
    assert response.status_code == 200
    assert response.json()["must_change_password"] is True
    return client


def change(client: ApiClient, current: str, new: str):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/auth/password", json={"current_password": current, "new_password": new}
    )


def test_must_change_password_blocks_every_other_endpoint(
    app: FastAPI, temp_client: ApiClient
) -> None:
    gated = [
        e
        for e in api_endpoints(app)
        if e not in PUBLIC_ENDPOINTS and e not in ALLOWED_DURING_FORCED_CHANGE
    ]
    assert ("GET", "/api/me") in gated

    for method, path in gated:
        response = temp_client.request(method, concrete(path), json={})
        assert response.status_code == 403, (method, path, response.status_code)
        assert error_code(response) == "password_change_required", (method, path)


def test_logout_is_allowed_during_forced_change(temp_client: ApiClient) -> None:
    assert temp_client.post("/api/auth/logout").status_code == 204


def test_forced_change_unlocks_the_account_and_is_audited(
    temp_client: ApiClient, temp_user: User, audit_events: AuditReader
) -> None:
    response = change(temp_client, STRONG_PASSWORD, OTHER_PASSWORD)

    assert response.status_code == 204
    assert temp_client.get("/api/me").status_code == 200
    [event] = audit_events.of("auth.password_change_forced")
    assert event.actor_user_id == temp_user.id
    assert event.outcome == "success"
    assert audit_events.of("auth.password_change") == []


def test_temporary_password_reused_as_new_password_is_refused(
    temp_client: ApiClient,
) -> None:
    response = change(temp_client, STRONG_PASSWORD, STRONG_PASSWORD)

    assert response.status_code == 422
    assert error_code(response) == "password_reuse"
    assert error_code(temp_client.get("/api/me")) == "password_change_required"


def test_voluntary_change_requires_the_current_password(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    user = make_user(db, "bob@example.com", now=clock.now)
    client = ApiClient(app)
    client.login("bob@example.com")

    wrong = change(client, OTHER_PASSWORD, THIRD_PASSWORD)

    assert wrong.status_code == 400
    assert error_code(wrong) == "invalid_current_password"
    db.expire_all()
    stored = db.get(User, user.id)
    assert stored is not None
    assert passwords.verify_password(stored.password_hash, STRONG_PASSWORD)
    [denied] = audit_events.of("auth.password_change")
    assert denied.outcome == "denied"


def test_voluntary_change_updates_the_hash_and_is_audited(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    user = make_user(db, "bob@example.com", now=clock.now)
    client = ApiClient(app)
    client.login("bob@example.com")

    assert change(client, STRONG_PASSWORD, OTHER_PASSWORD).status_code == 204

    db.expire_all()
    stored = db.get(User, user.id)
    assert stored is not None
    assert passwords.verify_password(stored.password_hash, OTHER_PASSWORD)
    assert ApiClient(app).login("bob@example.com", STRONG_PASSWORD).status_code == 401
    assert ApiClient(app).login("bob@example.com", OTHER_PASSWORD).status_code == 200
    [event] = audit_events.of("auth.password_change")
    assert event.outcome == "success"


def test_password_change_invalidates_other_sessions_keeps_current(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    make_user(db, "bob@example.com", now=clock.now)
    laptop, phone = ApiClient(app), ApiClient(app)
    laptop.login("bob@example.com")
    phone.login("bob@example.com")

    assert change(laptop, STRONG_PASSWORD, OTHER_PASSWORD).status_code == 204

    assert laptop.get("/api/me").status_code == 200
    assert phone.get("/api/me").status_code == 401


@pytest.mark.parametrize("new_password", ["short", "x" * 129])
def test_new_password_must_satisfy_policy(temp_client: ApiClient, new_password: str) -> None:
    response = change(temp_client, STRONG_PASSWORD, new_password)

    assert response.status_code == 422
    assert error_code(response) == "password_policy"
    assert new_password not in response.text


def test_change_password_requires_session(app: FastAPI) -> None:
    response = ApiClient(app).post(
        "/api/auth/password",
        json={"current_password": STRONG_PASSWORD, "new_password": OTHER_PASSWORD},
    )

    assert response.status_code == 401


def test_change_password_requires_csrf(temp_client: ApiClient) -> None:
    response = temp_client.http.post(
        "/api/auth/password",
        json={"current_password": STRONG_PASSWORD, "new_password": OTHER_PASSWORD},
    )

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"


def test_wrong_current_password_counts_toward_lockout(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    make_user(db, "bob@example.com", now=clock.now)
    client = ApiClient(app)
    client.login("bob@example.com")

    for _ in range(5):
        assert change(client, OTHER_PASSWORD, THIRD_PASSWORD).status_code == 400

    response = change(client, STRONG_PASSWORD, THIRD_PASSWORD)
    assert response.status_code == 429
    assert error_code(response) == "login_locked"
    assert ApiClient(app).login("bob@example.com").status_code == 429


def test_password_values_never_appear_in_audit_events(
    temp_client: ApiClient, audit_events: AuditReader
) -> None:
    change(temp_client, STRONG_PASSWORD, "short")
    change(temp_client, STRONG_PASSWORD, OTHER_PASSWORD)

    dump = repr([(e.action, e.target_id, e.metadata_) for e in audit_events.all()])
    for secret in (STRONG_PASSWORD, OTHER_PASSWORD, "argon2"):
        assert secret not in dump
