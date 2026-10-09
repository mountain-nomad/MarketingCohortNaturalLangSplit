"""Regression tests for the independent code review of feature/authentication-rbac."""

import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit import cli as root_cli
from cohortsplit.audit.service import AuditService
from cohortsplit.auth import cli as auth_cli
from cohortsplit.auth import lockout, user_admin
from cohortsplit.auth import roles as role_service
from cohortsplit.auth.errors import ApiError
from cohortsplit.auth.models import LoginThrottle, RolePermission, User
from cohortsplit.auth.service import AuthService
from cohortsplit.config import Settings
from tests.auth.conftest import AuditReader
from tests.auth.helpers import (
    OTHER_PASSWORD,
    STRONG_PASSWORD,
    ApiClient,
    FakeClock,
    admin_role,
    error_code,
    make_role,
    make_user,
)


def logged_in(app: FastAPI, email: str) -> ApiClient:
    client = ApiClient(app)
    assert client.login(email).status_code == 200
    return client


# --- Critical 1: lockout must hold under concurrent guessing ------------------------------


def test_concurrent_wrong_guesses_are_capped_by_the_lockout(
    engine: Engine, settings: Settings, db: Session, clock: FakeClock
) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("needs real concurrent transactions (PostgreSQL run)")
    make_user(db, "alice@example.com", now=clock.now)
    factory = sessionmaker(engine, expire_on_commit=False)
    service = AuthService(settings, AuditService(factory))
    start = threading.Barrier(16)

    def attempt(password: str) -> int:
        start.wait()
        with factory() as session:
            try:
                service.login(
                    session,
                    email="alice@example.com",
                    password=password,
                    now=clock.now,
                    request_id=None,
                )
            except ApiError as exc:
                return exc.status_code
            return 200

    # 15 wrong guesses and the right password, all racing.
    guesses = [OTHER_PASSWORD] * 15 + [STRONG_PASSWORD]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, guesses))

    evaluated = [r for r in results if r != 429]
    assert len(evaluated) <= 5 + 1, results  # at most max_failures wrong + maybe the right one
    assert results.count(401) <= 5, results
    with factory() as session:
        throttle = session.get(LoginThrottle, "alice@example.com")
        assert throttle is not None
        if 200 not in results:
            assert throttle.locked_until is not None


def test_failure_counter_never_loses_updates_from_other_sessions(
    engine: Engine, settings: Settings, db: Session, clock: FakeClock
) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("needs two independent connections (PostgreSQL run)")
    email = "alice@example.com"
    policy = lockout.LockoutPolicy.from_settings(settings)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as first:
        lockout.register_failure(first, email, clock.now, policy)
        first.commit()

    with factory() as a, factory() as b:
        assert lockout.locked_for(a, email, clock.now) is None  # A caches the row (count 1)
        lockout.register_failure(b, email, clock.now, policy)  # B: 2, committed
        b.commit()
        lockout.register_failure(a, email, clock.now, policy)  # A must see 2, write 3
        a.commit()

    with factory() as check:
        row = check.get(LoginThrottle, email)
        assert row is not None
        assert row.failure_count == 3


# --- Important 2: the CLI must not hold the Admin row lock while prompting ----------------


def test_create_admin_does_not_lock_admin_role_while_prompting(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("row locks are PostgreSQL-only")
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(auth_cli, "session_factory_from_settings", lambda: factory)
    url = make_url(str(engine.url.render_as_string(hide_password=False)))
    lock_free: list[bool] = []

    def prompt(_label: str = "") -> str:
        with psycopg.connect(
            host=url.host,
            port=url.port,
            dbname=url.database,
            user=url.username,
            password=url.password,
            autocommit=False,
        ) as other:
            try:
                other.execute("SELECT id FROM roles WHERE is_system FOR UPDATE NOWAIT")
                lock_free.append(True)
            except psycopg.errors.LockNotAvailable:
                lock_free.append(False)
            other.rollback()
        return STRONG_PASSWORD

    monkeypatch.setattr(auth_cli.getpass, "getpass", prompt)

    assert root_cli.main(["create-admin", "--email", "root@example.com"]) == 0
    assert lock_free and all(lock_free)


# --- Important 3: the policy layer enforces the admin-only rule itself ---------------------


def test_admin_only_permission_rows_on_custom_roles_confer_nothing(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    role = make_role(db, "Tampered", ["cohort.create"], now=clock.now)
    db.add(RolePermission(role_id=role.id, permission_key="user.read"))
    db.add(RolePermission(role_id=role.id, permission_key="role.update"))
    db.commit()
    make_user(db, "mallory@example.com", now=clock.now, roles=[role])
    client = logged_in(app, "mallory@example.com")

    assert client.get("/api/me").json()["permissions"] == ["cohort.create"]
    assert client.get("/api/admin/users").status_code == 403
    assert client.patch(f"/api/admin/roles/{role.id}", json={"name": "x"}).status_code == 403


# --- Important 4: security-relevant refusals are audited -----------------------------------


def test_last_admin_refusal_is_audited(
    admin_client: ApiClient, admin_user: User, audit_events: AuditReader
) -> None:
    assert admin_client.post(f"/api/admin/users/{admin_user.id}/deactivate").status_code == 409

    [event] = audit_events.of("user.deactivate")
    assert event.outcome == "denied"
    assert event.metadata_["reason"] == "last_admin"
    assert event.target_id == str(admin_user.id)


def test_last_admin_demotion_refusal_is_audited(
    admin_client: ApiClient, admin_user: User, audit_events: AuditReader
) -> None:
    admin_client.put(f"/api/admin/users/{admin_user.id}/roles", json={"role_ids": []})

    [event] = audit_events.of("role.unassign")
    assert (event.outcome, event.metadata_["reason"]) == ("denied", "last_admin")


def test_system_role_refusals_are_audited(
    admin_client: ApiClient, db: Session, audit_events: AuditReader
) -> None:
    admin_id = admin_role(db).id
    admin_client.patch(f"/api/admin/roles/{admin_id}", json={"name": "Root"})
    admin_client.delete(f"/api/admin/roles/{admin_id}?confirm=true")

    assert [(e.outcome, e.metadata_["reason"]) for e in audit_events.of("role.update")] == [
        ("denied", "system_role_protected")
    ]
    assert [e.outcome for e in audit_events.of("role.delete")] == ["denied"]


def test_admin_only_permission_refusal_is_audited(
    admin_client: ApiClient, audit_events: AuditReader
) -> None:
    admin_client.post("/api/admin/roles", json={"name": "Escalate", "permissions": ["user.read"]})

    [event] = audit_events.of("role.create")
    assert event.outcome == "denied"
    assert event.metadata_["reason"] == "permission_not_grantable"
    assert event.metadata_["permissions"] == ["user.read"]


def test_csrf_refusal_is_audited(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    user = make_user(db, "alice@example.com", now=clock.now)
    client = logged_in(app, "alice@example.com")

    assert client.http.post("/api/auth/logout").status_code == 403

    [event] = audit_events.of("access.denied")
    assert event.actor_user_id == user.id
    assert event.metadata_["reason"] == "csrf_failed"
    assert event.target_id == "POST /api/auth/logout"


def test_password_change_required_refusal_is_audited(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    make_user(db, "temp@example.com", now=clock.now, must_change_password=True)
    client = logged_in(app, "temp@example.com")

    client.get("/api/me")

    [event] = audit_events.of("access.denied")
    assert event.metadata_["reason"] == "password_change_required"


# --- Important 5: correct statuses under races and odd input -------------------------------


def test_concurrent_duplicate_role_name_is_409_not_500(
    admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_client.post("/api/admin/roles", json={"name": "Marketer"})
    # Simulate the race: the pre-check passes, the unique index catches it.
    monkeypatch.setattr(role_service, "_ensure_name_free", lambda *a, **k: None)

    response = admin_client.post("/api/admin/roles", json={"name": "marketer"})

    assert response.status_code == 409
    assert error_code(response) == "role_name_taken"


def test_concurrent_duplicate_email_is_409(
    admin_client: ApiClient, db: Session, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user(db, "alice@example.com", now=clock.now)
    monkeypatch.setattr(user_admin, "find_by_email", lambda *a, **k: None)

    response = admin_client.post(
        "/api/admin/users",
        json={
            "email": "alice@example.com",
            "display_name": "Alice",
            "temporary_password": "temporary password 2026",
        },
    )

    assert response.status_code == 409
    assert error_code(response) == "email_taken"


def test_non_unique_integrity_errors_are_not_reported_as_email_taken(
    admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # e.g. Python and PostgreSQL disagree on lower(): the CHECK constraint fires.
    monkeypatch.setattr(user_admin, "validate_email", lambda email: "Mixed@Example.com")

    response = admin_client.post(
        "/api/admin/users",
        json={
            "email": "mixed@example.com",
            "display_name": "Mixed",
            "temporary_password": "temporary password 2026",
        },
    )

    assert response.status_code == 422
    assert error_code(response) == "invalid_email"


def test_create_admin_with_very_long_local_part(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(auth_cli, "session_factory_from_settings", lambda: factory)
    monkeypatch.setattr(auth_cli.getpass, "getpass", lambda _p="": STRONG_PASSWORD)
    email = "a" * 240 + "@example.com"

    assert root_cli.main(["create-admin", "--email", email]) == 0

    user = db.query(User).filter_by(email=email).one()
    assert 1 <= len(user.display_name) <= 200


# --- Minor 9: deleting an assigned role records per-user removals --------------------------


def test_deleting_an_assigned_role_records_each_unassignment(
    admin_client: ApiClient, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    role = make_role(db, "Auditor", ["audit.read"], now=clock.now)
    alice = make_user(db, "alice@example.com", now=clock.now, roles=[role])
    bob = make_user(db, "bob@example.com", now=clock.now, roles=[role])

    admin_client.delete(f"/api/admin/roles/{role.id}?confirm=true")

    removed = audit_events.of("role.unassign")
    assert sorted(e.target_id for e in removed) == sorted([str(alice.id), str(bob.id)])
    assert all(e.metadata_["role_name"] == "Auditor" for e in removed)
