"""Server-side sessions, expiry, logout, CSRF, /api/me and fail-closed DB errors (FR-A1, AC-A5)."""

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from cohortsplit.app import create_app
from cohortsplit.auth.catalog import ALL_PERMISSIONS
from cohortsplit.auth.models import AuthSession, User
from cohortsplit.config import Settings
from tests.auth.conftest import AuditReader
from tests.auth.helpers import (
    ApiClient,
    FakeClock,
    admin_role,
    error_code,
    make_role,
    make_user,
)


@pytest.fixture
def alice(db: Session, clock: FakeClock) -> User:
    role = make_role(db, "Marketer", ["cohort.create", "cohort.export"], now=clock.now)
    return make_user(db, "alice@example.com", now=clock.now, roles=[role], display_name="Alice")


@pytest.fixture
def alice_client(app: FastAPI, alice: User) -> ApiClient:
    client = ApiClient(app)
    assert client.login("alice@example.com").status_code == 200
    return client


def test_me_returns_identity_roles_and_effective_permissions(
    alice_client: ApiClient, alice: User
) -> None:
    response = alice_client.get("/api/me")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == alice.id
    assert body["email"] == "alice@example.com"
    assert body["display_name"] == "Alice"
    assert body["is_admin"] is False
    assert [r["name"] for r in body["roles"]] == ["Marketer"]
    assert body["permissions"] == ["cohort.create", "cohort.export"]
    assert "password_hash" not in response.text


def test_me_for_admin_lists_every_catalog_permission(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    make_user(db, "root@example.com", now=clock.now, roles=[admin_role(db)])
    client = ApiClient(app)
    client.login("root@example.com")

    body = client.get("/api/me").json()

    assert body["is_admin"] is True
    assert set(body["permissions"]) == ALL_PERMISSIONS


def test_me_without_session_is_refused(app: FastAPI) -> None:
    response = ApiClient(app).get("/api/me")

    assert response.status_code == 401
    assert error_code(response) == "not_authenticated"


def test_garbage_session_cookie_is_refused(app: FastAPI) -> None:
    client = ApiClient(app)
    client.http.cookies.set("cohortsplit_session", "forged-token", path="/api")

    assert client.get("/api/me").status_code == 401


def test_session_token_is_stored_only_as_a_digest(alice_client: ApiClient, db: Session) -> None:
    token = alice_client.http.cookies.get("cohortsplit_session")
    assert token

    stored = db.execute(select(AuthSession)).scalar_one()

    assert stored.token_hash != token
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert alice_client.csrf_token not in (stored.token_hash, stored.csrf_token_hash)


def test_logout_invalidates_session_server_side(alice_client: ApiClient) -> None:
    stolen_cookie = alice_client.http.cookies.get("cohortsplit_session")

    response = alice_client.post("/api/auth/logout")

    assert response.status_code == 204
    replay = ApiClient(alice_client.http.app)  # type: ignore[arg-type]
    replay.http.cookies.set("cohortsplit_session", stolen_cookie or "", path="/api")
    assert replay.get("/api/me").status_code == 401
    assert alice_client.get("/api/me").status_code == 401


def test_logout_clears_cookies_and_is_audited(
    alice_client: ApiClient, audit_events: AuditReader, alice: User
) -> None:
    response = alice_client.post("/api/auth/logout")

    cookies = " ".join(response.headers.get_list("set-cookie"))
    assert "cohortsplit_session=" in cookies
    assert "cohortsplit_csrf=" in cookies
    [event] = audit_events.of("auth.logout")
    assert event.actor_user_id == alice.id
    assert event.outcome == "success"


def test_logout_without_session_is_refused(app: FastAPI) -> None:
    assert ApiClient(app).post("/api/auth/logout").status_code == 401


def test_idle_timeout_expires_session(alice_client: ApiClient, clock: FakeClock) -> None:
    clock.advance(hours=7, minutes=59)
    assert alice_client.get("/api/me").status_code == 200

    clock.advance(hours=8)

    response = alice_client.get("/api/me")
    assert response.status_code == 401
    assert error_code(response) == "not_authenticated"


def test_activity_extends_idle_timeout(alice_client: ApiClient, clock: FakeClock) -> None:
    for _ in range(4):
        clock.advance(hours=7)
        assert alice_client.get("/api/me").status_code == 200


def test_absolute_lifetime_expires_session_despite_activity(
    alice_client: ApiClient, clock: FakeClock
) -> None:
    for _ in range(23):  # 23 * 7h = 161h < 168h
        clock.advance(hours=7)
        assert alice_client.get("/api/me").status_code == 200

    clock.advance(hours=7)  # 168h

    assert alice_client.get("/api/me").status_code == 401


@pytest.mark.parametrize(
    "settings_overrides",
    [{"session_idle_timeout_minutes": 30, "session_max_lifetime_hours": 1}],
)
def test_session_lifetimes_are_configurable(alice_client: ApiClient, clock: FakeClock) -> None:
    clock.advance(minutes=29)
    assert alice_client.get("/api/me").status_code == 200
    clock.advance(minutes=29)
    assert alice_client.get("/api/me").status_code == 200

    clock.advance(minutes=2)  # 60 minutes after login

    assert alice_client.get("/api/me").status_code == 401


def test_sessions_are_independent(app: FastAPI, alice: User) -> None:
    first, second = ApiClient(app), ApiClient(app)
    first.login("alice@example.com")
    second.login("alice@example.com")

    first.post("/api/auth/logout")

    assert second.get("/api/me").status_code == 200


# --- CSRF -------------------------------------------------------------------------------


def test_state_changing_request_without_csrf_header_is_refused(
    alice_client: ApiClient,
) -> None:
    response = alice_client.http.post("/api/auth/logout")

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"
    assert alice_client.get("/api/me").status_code == 200  # still logged in


def test_state_changing_request_with_wrong_csrf_token_is_refused(
    alice_client: ApiClient,
) -> None:
    response = alice_client.post("/api/auth/logout", headers={"X-CSRF-Token": "x" * 43})

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"


def test_csrf_token_of_another_session_is_refused(app: FastAPI, alice: User) -> None:
    mine, other = ApiClient(app), ApiClient(app)
    mine.login("alice@example.com")
    other.login("alice@example.com")

    response = mine.post("/api/auth/logout", headers={"X-CSRF-Token": other.csrf_token or ""})

    assert response.status_code == 403


def test_safe_methods_do_not_need_csrf(alice_client: ApiClient) -> None:
    assert alice_client.http.get("/api/me").status_code == 200


# --- fail closed --------------------------------------------------------------------------


def test_deactivated_user_session_is_refused_even_if_session_row_survives(
    alice_client: ApiClient, alice: User, db: Session
) -> None:
    db.execute(User.__table__.update().where(User.id == alice.id).values(is_active=False))
    db.commit()

    assert alice_client.get("/api/me").status_code == 401


def test_db_unreachable_refuses_authenticated_requests_with_503(
    unreachable_appdb_env: pytest.MonkeyPatch,
) -> None:
    from cohortsplit.config import load_settings

    client = TestClient(create_app(load_settings()))
    client.cookies.set("cohortsplit_session", "any-token", path="/api")

    response = client.get("/api/me")

    assert response.status_code == 503
    assert error_code(response) == "service_unavailable"


def test_db_unreachable_login_is_refused_with_503(
    unreachable_appdb_env: pytest.MonkeyPatch,
) -> None:
    from cohortsplit.config import load_settings

    client = TestClient(create_app(load_settings()))

    response = client.post("/api/auth/login", json={"email": "a@example.com", "password": "x" * 12})

    assert response.status_code == 503


def test_settings_type_has_auth_defaults(clean_env: pytest.MonkeyPatch) -> None:
    settings = Settings(appdb_password=SecretStr("x"))

    assert settings.session_idle_timeout_minutes == 480
    assert settings.session_max_lifetime_hours == 168
    assert settings.login_max_failures == 5
    assert settings.login_failure_window_minutes == 15
    assert settings.login_lockout_minutes == 15
    assert settings.cookie_secure is None
    assert settings.api_docs_enabled is False
