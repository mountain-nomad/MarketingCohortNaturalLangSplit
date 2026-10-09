"""Login, generic failures and brute-force lockout (FR-A1, AC-A2, AC-A3)."""

import logging

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from cohortsplit.auth.models import AuthSession, User
from tests.auth.conftest import AuditReader
from tests.auth.helpers import (
    OTHER_PASSWORD,
    STRONG_PASSWORD,
    ApiClient,
    FakeClock,
    error_code,
    make_user,
)


@pytest.fixture
def alice(db: Session, clock: FakeClock) -> User:
    return make_user(db, "alice@example.com", now=clock.now, display_name="Alice")


def test_login_success_returns_user_csrf_token_and_session_cookie(
    app: FastAPI, alice: User
) -> None:
    client = ApiClient(app)

    response = client.login("alice@example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {"id": alice.id, "email": "alice@example.com", "display_name": "Alice"}
    assert body["must_change_password"] is False
    assert isinstance(body["csrf_token"], str)
    assert len(body["csrf_token"]) >= 32
    assert "cohortsplit_session" in response.cookies
    assert response.cookies["cohortsplit_csrf"] == body["csrf_token"]


def test_session_cookie_is_httponly_samesite_strict_and_not_secure_over_http(
    app: FastAPI, alice: User
) -> None:
    response = ApiClient(app).login("alice@example.com")

    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("cohortsplit_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("cohortsplit_csrf="))
    assert "HttpOnly" in session_cookie
    assert "SameSite=strict" in session_cookie or "SameSite=Strict" in session_cookie
    assert "Path=/api" in session_cookie
    assert "Secure" not in session_cookie
    assert "HttpOnly" not in csrf_cookie  # the SPA reads it to send X-CSRF-Token
    assert "samesite=strict" in csrf_cookie.lower()


def test_session_cookie_is_secure_over_https_in_auto_mode(app: FastAPI, alice: User) -> None:
    response = ApiClient(app, base_url="https://testserver").login("alice@example.com")

    session_cookie = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith("cohortsplit_session=")
    )
    assert "Secure" in session_cookie


@pytest.mark.parametrize("settings_overrides", [{"cookie_secure": True}])
def test_session_cookie_is_secure_when_configured(app: FastAPI, alice: User) -> None:
    response = ApiClient(app).login("alice@example.com")

    session_cookie = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith("cohortsplit_session=")
    )
    assert "Secure" in session_cookie


def test_login_email_is_case_insensitive(app: FastAPI, alice: User) -> None:
    assert ApiClient(app).login("  Alice@Example.COM ").status_code == 200


def test_wrong_password_and_unknown_email_get_identical_responses(
    app: FastAPI, alice: User
) -> None:
    wrong_password = ApiClient(app).login("alice@example.com", OTHER_PASSWORD)
    unknown_email = ApiClient(app).login("nobody@example.com", OTHER_PASSWORD)

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert error_code(wrong_password) == "invalid_credentials"
    assert "set-cookie" not in wrong_password.headers
    assert "set-cookie" not in unknown_email.headers


def test_inactive_user_gets_the_same_generic_failure(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    make_user(db, "gone@example.com", now=clock.now, is_active=False)

    inactive = ApiClient(app).login("gone@example.com")
    unknown = ApiClient(app).login("nobody@example.com")

    assert inactive.status_code == 401
    assert inactive.json() == unknown.json()


def test_login_failure_and_success_are_audited(
    app: FastAPI, alice: User, audit_events: AuditReader
) -> None:
    ApiClient(app).login("alice@example.com", OTHER_PASSWORD)
    ApiClient(app).login("alice@example.com")

    failed = audit_events.of("auth.login_failed")
    succeeded = audit_events.of("auth.login")
    assert len(failed) == 1
    assert failed[0].outcome == "denied"
    assert failed[0].actor_type == "anonymous"
    assert (failed[0].target_type, failed[0].target_id) == ("user", str(alice.id))
    assert failed[0].metadata_["reason"] == "invalid_credentials"
    assert len(succeeded) == 1
    assert succeeded[0].outcome == "success"
    assert succeeded[0].actor_user_id == alice.id


def test_unknown_email_failure_is_audited_without_the_raw_email(
    app: FastAPI, audit_events: AuditReader
) -> None:
    # People sometimes type a password into the email field: never store the raw value.
    ApiClient(app).login("hunter2-secret-typed-here", OTHER_PASSWORD)

    [event] = audit_events.of("auth.login_failed")
    assert event.target_id is None
    serialized = repr((event.target_type, event.target_id, event.metadata_))
    assert "hunter2-secret-typed-here" not in serialized
    assert len(event.metadata_["email_fingerprint"]) == 16


def test_successful_login_records_last_login(
    app: FastAPI, alice: User, db: Session, clock: FakeClock
) -> None:
    ApiClient(app).login("alice@example.com")

    db.expire_all()
    assert db.get(User, alice.id).last_login_at == clock.now  # type: ignore[union-attr]


def test_login_requires_json_body(app: FastAPI, alice: User) -> None:
    # A cross-site HTML form can only send form-encoded bodies (login CSRF).
    response = ApiClient(app).http.post(
        "/api/auth/login",
        data={"email": "alice@example.com", "password": STRONG_PASSWORD},
    )

    assert response.status_code == 422
    assert "set-cookie" not in response.headers


def test_validation_errors_never_echo_the_password(app: FastAPI) -> None:
    response = ApiClient(app).http.post(
        "/api/auth/login", json={"email": ["not", "a", "string"], "password": "p4ss-echo-check"}
    )

    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert "p4ss-echo-check" not in response.text


def test_password_never_logged(app: FastAPI, alice: User, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    ApiClient(app).login("alice@example.com", OTHER_PASSWORD)
    ApiClient(app).login("alice@example.com")

    assert STRONG_PASSWORD not in caplog.text
    assert OTHER_PASSWORD not in caplog.text


# --- lockout (AC-A3) -----------------------------------------------------------------------


def _fail(app: FastAPI, email: str, times: int) -> None:
    for _ in range(times):
        assert ApiClient(app).login(email, OTHER_PASSWORD).status_code == 401


def test_sixth_attempt_refused_even_with_correct_password_and_lockout_audited(
    app: FastAPI, alice: User, audit_events: AuditReader
) -> None:
    _fail(app, "alice@example.com", 5)

    response = ApiClient(app).login("alice@example.com", STRONG_PASSWORD)

    assert response.status_code == 429
    assert error_code(response) == "login_locked"
    assert int(response.headers["Retry-After"]) == 15 * 60
    assert response.json()["error"]["retry_after_seconds"] == 15 * 60
    assert "set-cookie" not in response.headers
    lockouts = audit_events.of("auth.lockout")
    assert len(lockouts) == 1
    assert (lockouts[0].target_type, lockouts[0].target_id) == ("user", str(alice.id))
    refused = [e for e in audit_events.of("auth.login_failed") if e.metadata_["reason"] == "locked"]
    assert len(refused) == 1


def test_lockout_message_is_generic_for_known_and_unknown_accounts(
    app: FastAPI, alice: User
) -> None:
    _fail(app, "alice@example.com", 5)
    _fail(app, "nobody@example.com", 5)

    known = ApiClient(app).login("alice@example.com")
    unknown = ApiClient(app).login("nobody@example.com")

    assert known.status_code == unknown.status_code == 429
    assert known.json() == unknown.json()


def test_lockout_applies_to_unknown_emails(app: FastAPI) -> None:
    _fail(app, "nobody@example.com", 5)

    assert ApiClient(app).login("nobody@example.com", OTHER_PASSWORD).status_code == 429


def test_four_failures_do_not_lock(app: FastAPI, alice: User) -> None:
    _fail(app, "alice@example.com", 4)

    assert ApiClient(app).login("alice@example.com").status_code == 200


def test_lockout_expires_after_configured_minutes(
    app: FastAPI, alice: User, clock: FakeClock
) -> None:
    _fail(app, "alice@example.com", 5)
    clock.advance(minutes=14, seconds=59)
    assert ApiClient(app).login("alice@example.com").status_code == 429

    clock.advance(seconds=1)

    assert ApiClient(app).login("alice@example.com").status_code == 200


def test_retry_after_counts_down(app: FastAPI, alice: User, clock: FakeClock) -> None:
    _fail(app, "alice@example.com", 5)
    clock.advance(minutes=10)

    response = ApiClient(app).login("alice@example.com")

    assert int(response.headers["Retry-After"]) == 5 * 60


def test_failures_spread_beyond_the_window_do_not_lock(
    app: FastAPI, alice: User, clock: FakeClock
) -> None:
    _fail(app, "alice@example.com", 4)
    clock.advance(minutes=16)
    _fail(app, "alice@example.com", 4)

    assert ApiClient(app).login("alice@example.com").status_code == 200


def test_successful_login_resets_the_failure_count(app: FastAPI, alice: User) -> None:
    _fail(app, "alice@example.com", 4)
    assert ApiClient(app).login("alice@example.com").status_code == 200
    _fail(app, "alice@example.com", 4)

    assert ApiClient(app).login("alice@example.com").status_code == 200


def test_lockout_is_per_account(app: FastAPI, alice: User, db: Session, clock: FakeClock) -> None:
    make_user(db, "bob@example.com", now=clock.now)
    _fail(app, "alice@example.com", 5)

    assert ApiClient(app).login("bob@example.com").status_code == 200


def test_lockout_case_variants_share_the_counter(app: FastAPI, alice: User) -> None:
    variants = (
        "alice@example.com",
        "ALICE@example.com",
        "Alice@Example.com",
        "alice@EXAMPLE.com",
        " alice@example.com ",
    )
    for email in variants:
        assert ApiClient(app).login(email, OTHER_PASSWORD).status_code == 401

    assert ApiClient(app).login("alice@example.com").status_code == 429


@pytest.mark.parametrize(
    "settings_overrides",
    [{"login_max_failures": 3, "login_lockout_minutes": 2, "login_failure_window_minutes": 1}],
)
def test_lockout_thresholds_are_configurable(app: FastAPI, alice: User, clock: FakeClock) -> None:
    _fail(app, "alice@example.com", 3)
    assert ApiClient(app).login("alice@example.com").status_code == 429

    clock.advance(minutes=2)

    assert ApiClient(app).login("alice@example.com").status_code == 200


def test_no_session_is_created_on_failed_login(app: FastAPI, alice: User, db: Session) -> None:
    _fail(app, "alice@example.com", 2)

    assert db.execute(select(AuthSession)).first() is None
