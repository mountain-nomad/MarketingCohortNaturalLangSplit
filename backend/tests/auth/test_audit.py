"""Audit log: coverage of event types, append-only API, no secrets, reader API (FR-A5)."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from cohortsplit.audit.service import Actor, AuditEventIn, AuditService
from cohortsplit.auth.models import Role, User
from tests.auth.conftest import AuditReader
from tests.auth.helpers import (
    EXPORT_TEST_PATH,
    OTHER_PASSWORD,
    STRONG_PASSWORD,
    THIRD_PASSWORD,
    ApiClient,
    FakeClock,
    error_code,
    export_test_router,
    make_role,
    make_user,
)
from tests.constants import APPDB_PASSWORD

TEMP_PASSWORD = "temporary password 2026"


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    app.include_router(export_test_router())
    return app


def logged_in(app: FastAPI, email: str, password: str = STRONG_PASSWORD) -> ApiClient:
    client = ApiClient(app)
    assert client.login(email, password).status_code == 200
    return client


# --- reader API -------------------------------------------------------------------------


def test_audit_api_requires_audit_read(app: FastAPI, db: Session, clock: FakeClock) -> None:
    auditor = make_role(db, "Auditor", ["audit.read"], now=clock.now)
    marketer = make_role(db, "Marketer", ["cohort.create", "cohort.export"], now=clock.now)
    make_user(db, "auditor@example.com", now=clock.now, roles=[auditor])
    make_user(db, "marketer@example.com", now=clock.now, roles=[marketer])

    assert ApiClient(app).get("/api/audit/events").status_code == 401
    denied = logged_in(app, "marketer@example.com").get("/api/audit/events")
    assert denied.status_code == 403
    assert error_code(denied) == "permission_denied"
    assert logged_in(app, "auditor@example.com").get("/api/audit/events").status_code == 200


def test_no_api_mutates_audit_events(app: FastAPI, admin_client: ApiClient) -> None:
    audit_routes = [
        r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/audit")
    ]
    assert audit_routes
    assert all(r.methods <= {"GET", "HEAD"} for r in audit_routes)

    event_id = admin_client.get("/api/audit/events").json()["items"][0]["id"]
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        for path in ("/api/audit/events", f"/api/audit/events/{event_id}"):
            response = admin_client.request(method, path, json={"action": "tampered"})
            assert response.status_code in (404, 405), (method, path)

    assert all(
        e["action"] != "tampered" for e in admin_client.get("/api/audit/events").json()["items"]
    )


def test_events_are_listed_newest_first_with_actor_and_request_id(
    app: FastAPI, admin_client: ApiClient, admin_user: User, clock: FakeClock
) -> None:
    clock.advance(minutes=1)
    admin_client.post(
        "/api/admin/roles",
        json={"name": "Analyst", "permissions": []},
        headers={"X-Request-ID": "req-abc-123"},
    )

    body = admin_client.get("/api/audit/events").json()

    first = body["items"][0]
    assert first["action"] == "role.create"
    assert first["actor_type"] == "user"
    assert first["actor_user_id"] == admin_user.id
    assert first["actor_email"] == "root@example.com"
    assert first["outcome"] == "success"
    assert first["target_type"] == "role"
    assert first["request_id"] == "req-abc-123"
    assert first["occurred_at"].startswith("2026-03-02T09:01")
    assert body["items"][-1]["action"] == "auth.login"


def test_pagination(admin_client: ApiClient, db: Session, clock: FakeClock) -> None:
    for i in range(7):
        admin_client.post("/api/admin/roles", json={"name": f"Role {i}", "permissions": []})

    page1 = admin_client.get("/api/audit/events?limit=3&offset=0").json()
    page3 = admin_client.get("/api/audit/events?limit=3&offset=6").json()

    assert page1["total"] == 8  # 1 login + 7 role.create
    assert (page1["limit"], page1["offset"]) == (3, 0)
    assert len(page1["items"]) == 3
    assert len(page3["items"]) == 2
    ids = [e["id"] for e in page1["items"]] + [e["id"] for e in page3["items"]]
    assert len(set(ids)) == 5


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "outcome=maybe"])
def test_invalid_paging_and_filters_are_rejected(admin_client: ApiClient, query: str) -> None:
    assert admin_client.get(f"/api/audit/events?{query}").status_code == 422


def test_filters_by_actor_action_outcome_and_date(
    app: FastAPI, admin_client: ApiClient, db: Session, clock: FakeClock
) -> None:
    make_user(db, "alice@example.com", now=clock.now)
    clock.advance(hours=1)
    ApiClient(app).login("alice@example.com", OTHER_PASSWORD)  # denied, anonymous
    clock.advance(hours=1)
    logged_in(app, "alice@example.com")  # success by alice
    clock.advance(hours=1)
    admin_client.post("/api/admin/roles", json={"name": "Analyst", "permissions": []})

    def actions(query: str) -> list[str]:
        response = admin_client.get(f"/api/audit/events?{query}")
        assert response.status_code == 200, response.text
        return [e["action"] for e in response.json()["items"]]

    assert actions("actor_email=ALICE@example.com") == ["auth.login"]
    assert actions("actor_type=anonymous") == ["auth.login_failed"]
    assert actions("action=role.create") == ["role.create"]
    assert actions("outcome=denied") == ["auth.login_failed"]
    assert actions("since=2026-03-02T10:30:00Z&until=2026-03-02T11:30:00Z") == ["auth.login"]
    assert actions("action=auth.login&actor_email=root@example.com") == ["auth.login"]


# --- event coverage (AC-A18) --------------------------------------------------------------


class Scenario:
    def __init__(self, app: FastAPI, db: Session, clock: FakeClock, admin: ApiClient) -> None:
        self.app, self.db, self.clock, self.admin = app, db, clock, admin
        self.role: Role = make_role(
            db, "Marketer", ["cohort.create", "cohort.export"], now=clock.now
        )
        self.user: User = make_user(db, "alice@example.com", now=clock.now)
        self.temp: User = make_user(
            db, "temp@example.com", now=clock.now, must_change_password=True
        )

    def client(self, email: str = "alice@example.com") -> ApiClient:
        return logged_in(self.app, email)


def _prepare_lockout(s: Scenario) -> None:
    for _ in range(4):
        ApiClient(s.app).login("alice@example.com", OTHER_PASSWORD)


EVENT_SCENARIOS: dict[str, tuple[Callable[[Scenario], None], Callable[[Scenario], object]]] = {
    "auth.login": (lambda s: None, lambda s: s.client()),
    "auth.login_failed": (
        lambda s: None,
        lambda s: ApiClient(s.app).login("alice@example.com", OTHER_PASSWORD),
    ),
    "auth.lockout": (
        _prepare_lockout,
        lambda s: ApiClient(s.app).login("alice@example.com", OTHER_PASSWORD),
    ),
    "auth.logout": (lambda s: None, lambda s: s.client().post("/api/auth/logout")),
    "auth.password_change": (
        lambda s: None,
        lambda s: s.client().post(
            "/api/auth/password",
            json={"current_password": STRONG_PASSWORD, "new_password": OTHER_PASSWORD},
        ),
    ),
    "auth.password_change_forced": (
        lambda s: None,
        lambda s: s.client("temp@example.com").post(
            "/api/auth/password",
            json={"current_password": STRONG_PASSWORD, "new_password": THIRD_PASSWORD},
        ),
    ),
    "user.create": (
        lambda s: None,
        lambda s: s.admin.post(
            "/api/admin/users",
            json={
                "email": "new@example.com",
                "display_name": "New",
                "temporary_password": TEMP_PASSWORD,
                "role_ids": [],
            },
        ),
    ),
    "user.update": (
        lambda s: None,
        lambda s: s.admin.patch(f"/api/admin/users/{s.user.id}", json={"display_name": "A"}),
    ),
    "user.deactivate": (
        lambda s: None,
        lambda s: s.admin.post(f"/api/admin/users/{s.user.id}/deactivate"),
    ),
    "user.reactivate": (
        lambda s: s.admin.post(f"/api/admin/users/{s.user.id}/deactivate"),
        lambda s: s.admin.post(f"/api/admin/users/{s.user.id}/reactivate"),
    ),
    "user.password_reset": (
        lambda s: None,
        lambda s: s.admin.post(
            f"/api/admin/users/{s.user.id}/reset-password",
            json={"temporary_password": TEMP_PASSWORD},
        ),
    ),
    "role.create": (
        lambda s: None,
        lambda s: s.admin.post("/api/admin/roles", json={"name": "Analyst", "permissions": []}),
    ),
    "role.update": (
        lambda s: None,
        lambda s: s.admin.patch(
            f"/api/admin/roles/{s.role.id}", json={"export_columns": ["public.users.phone"]}
        ),
    ),
    "role.delete": (
        lambda s: None,
        lambda s: s.admin.delete(f"/api/admin/roles/{s.role.id}"),
    ),
    "role.assign": (
        lambda s: None,
        lambda s: s.admin.put(
            f"/api/admin/users/{s.user.id}/roles", json={"role_ids": [s.role.id]}
        ),
    ),
    "role.unassign": (
        lambda s: s.admin.put(
            f"/api/admin/users/{s.user.id}/roles", json={"role_ids": [s.role.id]}
        ),
        lambda s: s.admin.put(f"/api/admin/users/{s.user.id}/roles", json={"role_ids": []}),
    ),
    "cohort.export": (
        lambda s: s.admin.put(
            f"/api/admin/users/{s.user.id}/roles", json={"role_ids": [s.role.id]}
        ),
        lambda s: s.client().post(EXPORT_TEST_PATH, json={"run_id": "r1", "columns": []}),
    ),
    "cohort.redownload": (
        lambda s: s.admin.put(
            f"/api/admin/users/{s.user.id}/roles", json={"role_ids": [s.role.id]}
        ),
        lambda s: s.client().post(
            EXPORT_TEST_PATH, json={"run_id": "r1", "columns": [], "redownload": True}
        ),
    ),
    "access.denied": (lambda s: None, lambda s: s.client().get("/api/admin/users")),
}


@pytest.mark.parametrize("action", sorted(EVENT_SCENARIOS))
def test_each_event_type_recorded_exactly_once(
    action: str,
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    admin_client: ApiClient,
    audit_events: AuditReader,
) -> None:
    scenario = Scenario(app, db, clock, admin_client)
    prepare, act = EVENT_SCENARIOS[action]
    prepare(scenario)
    before = len(audit_events.of(action))

    act(scenario)

    events = audit_events.of(action)
    assert len(events) == before + 1, action
    event = events[-1]
    assert event.actor_type in ("user", "cli", "anonymous")
    assert event.target_type
    assert event.outcome in ("success", "denied", "error")
    assert event.request_id
    assert event.occurred_at is not None


# --- never secrets (AC-A20) ---------------------------------------------------------------


def test_audit_events_never_contain_secrets(
    app: FastAPI, db: Session, clock: FakeClock, admin_client: ApiClient, audit_events: AuditReader
) -> None:
    role = make_role(db, "Marketer", ["cohort.create", "cohort.export"], now=clock.now)
    user = make_user(db, "alice@example.com", now=clock.now, roles=[role])
    secrets: set[str] = {STRONG_PASSWORD, OTHER_PASSWORD, TEMP_PASSWORD, APPDB_PASSWORD}

    ApiClient(app).login("alice@example.com", OTHER_PASSWORD)
    alice = logged_in(app, "alice@example.com")
    secrets |= {alice.csrf_token or "", alice.http.cookies.get("cohortsplit_session") or ""}
    alice.post(EXPORT_TEST_PATH, json={"run_id": "r1", "columns": ["public.users.phone"]})
    alice.post(EXPORT_TEST_PATH, json={"run_id": "r1", "columns": []})
    alice.post(
        "/api/auth/password",
        json={"current_password": STRONG_PASSWORD, "new_password": OTHER_PASSWORD},
    )
    admin_client.post(
        "/api/admin/users",
        json={
            "email": "new@example.com",
            "display_name": "New",
            "temporary_password": TEMP_PASSWORD,
            "role_ids": [role.id],
        },
    )
    admin_client.post(
        f"/api/admin/users/{user.id}/reset-password", json={"temporary_password": TEMP_PASSWORD}
    )
    secrets |= {
        admin_client.csrf_token or "",
        admin_client.http.cookies.get("cohortsplit_session") or "",
    }
    db.expire_all()
    stored_user = db.get(User, user.id)
    assert stored_user is not None
    secrets.add(stored_user.password_hash)

    events = audit_events.all()
    assert len(events) >= 8
    dump = repr(
        [
            (e.action, e.actor_type, e.target_type, e.target_id, e.request_id, e.metadata_)
            for e in events
        ]
    )
    for secret in filter(None, secrets):
        assert secret not in dump
    assert "$argon2" not in dump


def test_secret_looking_metadata_keys_are_redacted(
    app: FastAPI, clock: FakeClock, audit_events: AuditReader
) -> None:
    audit: AuditService = app.state.auth.audit

    audit.record_detached(
        AuditEventIn(
            action="test.event",
            outcome="success",
            actor=Actor.cli(),
            occurred_at=clock.now,
            target_type="test",
            metadata={
                "password": "leak-1",
                "nested": {"api_token": "leak-2", "ok": "visible"},
                "warehouse_dsn": "leak-3",
            },
        ),
        sensitive=True,
    )

    [event] = audit_events.of("test.event")
    assert "leak" not in repr(event.metadata_)
    assert event.metadata_["nested"]["ok"] == "visible"


# --- fail closed (BR-A6) ------------------------------------------------------------------


def test_sensitive_admin_change_is_rolled_back_when_audit_fails(
    admin_client: ApiClient, engine: Engine
) -> None:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE audit_events RENAME TO audit_events_offline"))
    try:
        response = admin_client.post(
            "/api/admin/roles", json={"name": "Shadow", "permissions": ["audit.read"]}
        )
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE audit_events_offline RENAME TO audit_events"))

    assert response.status_code == 503
    assert error_code(response) == "audit_unavailable"
    names = [r["name"] for r in admin_client.get("/api/admin/roles").json()["items"]]
    assert "Shadow" not in names


def test_non_sensitive_audit_failure_does_not_block_logout(
    app: FastAPI, db: Session, clock: FakeClock, request: pytest.FixtureRequest
) -> None:
    make_user(db, "alice@example.com", now=clock.now)
    client = logged_in(app, "alice@example.com")
    request.getfixturevalue("audit_store_down")

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me").status_code == 401


def test_denied_permission_checks_are_audited(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    user = make_user(db, "alice@example.com", now=clock.now)

    logged_in(app, "alice@example.com").get("/api/admin/users")

    [event] = audit_events.of("access.denied")
    assert event.actor_user_id == user.id
    assert event.outcome == "denied"
    assert event.target_id == "GET /api/admin/users"
    assert event.metadata_["permission"] == "user.read"


def test_occurred_at_uses_utc(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    make_user(db, "alice@example.com", now=clock.now)
    logged_in(app, "alice@example.com")

    [event] = audit_events.of("auth.login")
    assert event.occurred_at == clock.now
    assert event.occurred_at.utcoffset() == timedelta(0)
