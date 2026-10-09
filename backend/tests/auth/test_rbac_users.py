"""Admin user management (FR-A2, FR-A3, AC-A12, AC-A13, AC-A14b, AC-A24)."""

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from cohortsplit.auth.catalog import GRANTABLE_PERMISSIONS
from cohortsplit.auth.models import Role, User
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

TEMP_PASSWORD = "temporary password 2026"


@pytest.fixture
def marketer_role(db: Session, clock: FakeClock) -> Role:
    return make_role(db, "Marketer", ["cohort.create", "cohort.export"], now=clock.now)


def create_user(admin: ApiClient, **overrides: object):  # type: ignore[no-untyped-def]
    body: dict[str, object] = {
        "email": "new.user@example.com",
        "display_name": "New User",
        "temporary_password": TEMP_PASSWORD,
        "role_ids": [],
    }
    body.update(overrides)
    return admin.post("/api/admin/users", json=body)


def logged_in(app: FastAPI, email: str, password: str = STRONG_PASSWORD) -> ApiClient:
    client = ApiClient(app)
    assert client.login(email, password).status_code == 200
    return client


# --- listing ------------------------------------------------------------------------------


def test_admin_lists_users_with_roles(
    admin_client: ApiClient, admin_user: User, db: Session, clock: FakeClock, marketer_role: Role
) -> None:
    make_user(db, "alice@example.com", now=clock.now, roles=[marketer_role])

    response = admin_client.get("/api/admin/users")

    assert response.status_code == 200
    items = {u["email"]: u for u in response.json()["items"]}
    assert set(items) == {"root@example.com", "alice@example.com"}
    alice = items["alice@example.com"]
    assert alice["is_active"] is True
    assert alice["must_change_password"] is False
    assert [r["name"] for r in alice["roles"]] == ["Marketer"]
    for user in response.json()["items"]:
        assert set(user) == {
            "id",
            "email",
            "display_name",
            "is_active",
            "must_change_password",
            "roles",
            "created_at",
            "last_login_at",
        }
    assert "password_hash" not in response.text
    assert "argon2" not in response.text


def test_non_admin_cannot_list_users(app: FastAPI, db: Session, clock: FakeClock) -> None:
    # Even a role holding every grantable permission is not an admin.
    everything = make_role(db, "Power user", sorted(GRANTABLE_PERMISSIONS), now=clock.now)
    make_user(db, "power@example.com", now=clock.now, roles=[everything])
    client = logged_in(app, "power@example.com")

    response = client.get("/api/admin/users")

    assert response.status_code == 403
    assert error_code(response) == "permission_denied"


def test_get_single_user_and_404(admin_client: ApiClient, admin_user: User) -> None:
    assert admin_client.get(f"/api/admin/users/{admin_user.id}").json()["email"] == (
        "root@example.com"
    )
    missing = admin_client.get("/api/admin/users/999999")
    assert missing.status_code == 404
    assert error_code(missing) == "not_found"


# --- creation -----------------------------------------------------------------------------


def test_create_user_with_temporary_password_and_roles(
    admin_client: ApiClient,
    admin_user: User,
    marketer_role: Role,
    app: FastAPI,
    audit_events: AuditReader,
) -> None:
    response = create_user(admin_client, role_ids=[marketer_role.id], email="New.User@Example.com")

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.user@example.com"
    assert body["display_name"] == "New User"
    assert body["must_change_password"] is True
    assert [r["name"] for r in body["roles"]] == ["Marketer"]
    assert TEMP_PASSWORD not in response.text

    client = ApiClient(app)
    login = client.login("new.user@example.com", TEMP_PASSWORD)
    assert login.json()["must_change_password"] is True
    assert error_code(client.get("/api/me")) == "password_change_required"

    [created] = audit_events.of("user.create")
    assert created.actor_user_id == admin_user.id
    assert created.target_id == str(body["id"])
    [assigned] = audit_events.of("role.assign")
    assert assigned.metadata_["role_name"] == "Marketer"


def test_created_user_can_complete_forced_change_and_use_permissions(
    admin_client: ApiClient, marketer_role: Role, app: FastAPI
) -> None:
    create_user(admin_client, role_ids=[marketer_role.id])
    client = ApiClient(app)
    client.login("new.user@example.com", TEMP_PASSWORD)

    changed = client.post(
        "/api/auth/password",
        json={"current_password": TEMP_PASSWORD, "new_password": OTHER_PASSWORD},
    )

    assert changed.status_code == 204
    assert client.get("/api/me").json()["permissions"] == ["cohort.create", "cohort.export"]


@pytest.mark.parametrize("email", ["alice@example.com", "ALICE@example.com", " Alice@Example.Com "])
def test_duplicate_email_is_refused_case_insensitively(
    admin_client: ApiClient, db: Session, clock: FakeClock, email: str
) -> None:
    make_user(db, "alice@example.com", now=clock.now)

    response = create_user(admin_client, email=email)

    assert response.status_code == 409
    assert error_code(response) == "email_taken"


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "", "x" * 250 + "@example.com"])
def test_create_user_validates_email(admin_client: ApiClient, email: str) -> None:
    response = create_user(admin_client, email=email)

    assert response.status_code == 422
    assert error_code(response) in ("invalid_email", "validation_error")


@pytest.mark.parametrize("display_name", ["", "   ", "x" * 201])
def test_create_user_validates_display_name(admin_client: ApiClient, display_name: str) -> None:
    response = create_user(admin_client, display_name=display_name)

    assert response.status_code == 422


def test_create_user_enforces_password_policy(admin_client: ApiClient, db: Session) -> None:
    response = create_user(admin_client, temporary_password="too-short")

    assert response.status_code == 422
    assert error_code(response) == "password_policy"
    assert "too-short" not in response.text


def test_create_user_with_unknown_role_is_refused(admin_client: ApiClient, app: FastAPI) -> None:
    response = create_user(admin_client, role_ids=[999999])

    assert response.status_code == 422
    assert error_code(response) == "unknown_role"
    assert ApiClient(app).login("new.user@example.com", TEMP_PASSWORD).status_code == 401


def test_admin_can_create_another_admin(admin_client: ApiClient, db: Session, app: FastAPI) -> None:
    response = create_user(admin_client, role_ids=[admin_role(db).id])

    assert response.status_code == 201
    assert [r["name"] for r in response.json()["roles"]] == ["Admin"]


# --- updates ------------------------------------------------------------------------------


def test_update_display_name_is_audited(
    admin_client: ApiClient, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now, display_name="Alice")

    response = admin_client.patch(f"/api/admin/users/{alice.id}", json={"display_name": "Alicia"})

    assert response.status_code == 200
    assert response.json()["display_name"] == "Alicia"
    [event] = audit_events.of("user.update")
    assert event.metadata_["display_name"] == {"before": "Alice", "after": "Alicia"}


def test_email_cannot_be_changed_through_update(
    admin_client: ApiClient, db: Session, clock: FakeClock
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now)

    admin_client.patch(f"/api/admin/users/{alice.id}", json={"email": "evil@example.com"})

    assert admin_client.get(f"/api/admin/users/{alice.id}").json()["email"] == "alice@example.com"


def test_set_roles_assigns_and_removes_with_audit(
    admin_client: ApiClient,
    db: Session,
    clock: FakeClock,
    marketer_role: Role,
    audit_events: AuditReader,
    app: FastAPI,
) -> None:
    analyst = make_role(db, "Analyst", ["semantic_context.read"], now=clock.now)
    alice = make_user(db, "alice@example.com", now=clock.now, roles=[marketer_role])
    alice_client = logged_in(app, "alice@example.com")

    response = admin_client.put(
        f"/api/admin/users/{alice.id}/roles", json={"role_ids": [analyst.id]}
    )

    assert response.status_code == 200
    assert [r["name"] for r in response.json()["roles"]] == ["Analyst"]
    assert alice_client.get("/api/me").json()["permissions"] == ["semantic_context.read"]
    [assigned] = audit_events.of("role.assign")
    [removed] = audit_events.of("role.unassign")
    assert (assigned.target_id, assigned.metadata_["role_name"]) == (str(alice.id), "Analyst")
    assert (removed.target_id, removed.metadata_["role_name"]) == (str(alice.id), "Marketer")


def test_set_roles_with_unknown_role_changes_nothing(
    admin_client: ApiClient, db: Session, clock: FakeClock, marketer_role: Role
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now, roles=[marketer_role])

    response = admin_client.put(
        f"/api/admin/users/{alice.id}/roles", json={"role_ids": [marketer_role.id, 424242]}
    )

    assert response.status_code == 422
    assert error_code(response) == "unknown_role"
    roles = admin_client.get(f"/api/admin/users/{alice.id}").json()["roles"]
    assert [r["name"] for r in roles] == ["Marketer"]


def test_user_with_zero_roles_logs_in_with_no_permissions(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    make_user(db, "nobody@example.com", now=clock.now)
    client = logged_in(app, "nobody@example.com")

    me = client.get("/api/me").json()

    assert me["roles"] == []
    assert me["permissions"] == []
    assert me["is_admin"] is False


# --- deactivation -------------------------------------------------------------------------


def test_deactivated_user_sessions_refused_on_next_request(
    admin_client: ApiClient,
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    audit_events: AuditReader,
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now)
    alice_client = logged_in(app, "alice@example.com")

    response = admin_client.post(f"/api/admin/users/{alice.id}/deactivate")

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert alice_client.get("/api/me").status_code == 401
    assert ApiClient(app).login("alice@example.com").status_code == 401
    [event] = audit_events.of("user.deactivate")
    assert event.target_id == str(alice.id)


def test_deactivated_user_is_kept_not_deleted(
    admin_client: ApiClient, db: Session, clock: FakeClock
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now)
    admin_client.post(f"/api/admin/users/{alice.id}/deactivate")

    listed = {u["email"]: u for u in admin_client.get("/api/admin/users").json()["items"]}

    assert listed["alice@example.com"]["is_active"] is False


def test_reactivated_user_can_log_in_again(
    admin_client: ApiClient,
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    audit_events: AuditReader,
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now, is_active=False)

    response = admin_client.post(f"/api/admin/users/{alice.id}/reactivate")

    assert response.status_code == 200
    assert ApiClient(app).login("alice@example.com").status_code == 200
    assert len(audit_events.of("user.reactivate")) == 1


def test_last_active_admin_cannot_be_deactivated(admin_client: ApiClient, admin_user: User) -> None:
    response = admin_client.post(f"/api/admin/users/{admin_user.id}/deactivate")

    assert response.status_code == 409
    assert error_code(response) == "last_admin"
    assert admin_client.get("/api/me").status_code == 200


def test_last_active_admin_cannot_be_demoted(
    admin_client: ApiClient, admin_user: User, marketer_role: Role
) -> None:
    response = admin_client.put(
        f"/api/admin/users/{admin_user.id}/roles", json={"role_ids": [marketer_role.id]}
    )

    assert response.status_code == 409
    assert error_code(response) == "last_admin"
    assert admin_client.get("/api/me").json()["is_admin"] is True


def test_inactive_admins_do_not_count_toward_the_last_admin_rule(
    admin_client: ApiClient, admin_user: User, db: Session, clock: FakeClock
) -> None:
    make_user(db, "old.root@example.com", now=clock.now, roles=[admin_role(db)], is_active=False)

    response = admin_client.put(f"/api/admin/users/{admin_user.id}/roles", json={"role_ids": []})

    assert response.status_code == 409


def test_admin_can_demote_self_when_another_active_admin_exists(
    admin_client: ApiClient, admin_user: User, db: Session, clock: FakeClock
) -> None:
    make_user(db, "second.root@example.com", now=clock.now, roles=[admin_role(db)])

    response = admin_client.put(f"/api/admin/users/{admin_user.id}/roles", json={"role_ids": []})

    assert response.status_code == 200
    # Takes effect on the next request.
    assert admin_client.get("/api/admin/users").status_code == 403
    assert admin_client.get("/api/me").json()["is_admin"] is False


def test_admin_can_deactivate_another_admin_when_not_last(
    admin_client: ApiClient, db: Session, clock: FakeClock
) -> None:
    other = make_user(db, "second.root@example.com", now=clock.now, roles=[admin_role(db)])

    assert admin_client.post(f"/api/admin/users/{other.id}/deactivate").status_code == 200


def test_reactivating_restores_the_admin_count(
    admin_client: ApiClient, admin_user: User, db: Session, clock: FakeClock
) -> None:
    other = make_user(
        db, "second.root@example.com", now=clock.now, roles=[admin_role(db)], is_active=False
    )
    admin_client.post(f"/api/admin/users/{other.id}/reactivate")

    assert admin_client.post(f"/api/admin/users/{admin_user.id}/deactivate").status_code == 200


# --- password reset -----------------------------------------------------------------------


def test_reset_password_forces_change_revokes_sessions_and_is_audited(
    admin_client: ApiClient,
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    audit_events: AuditReader,
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now)
    alice_client = logged_in(app, "alice@example.com")

    response = admin_client.post(
        f"/api/admin/users/{alice.id}/reset-password",
        json={"temporary_password": TEMP_PASSWORD},
    )

    assert response.status_code == 200
    assert response.json()["must_change_password"] is True
    assert TEMP_PASSWORD not in response.text
    assert alice_client.get("/api/me").status_code == 401
    assert ApiClient(app).login("alice@example.com", STRONG_PASSWORD).status_code == 401
    fresh = ApiClient(app)
    assert fresh.login("alice@example.com", TEMP_PASSWORD).json()["must_change_password"] is True
    [event] = audit_events.of("user.password_reset")
    assert event.actor_user_id is not None


def test_reset_password_enforces_policy(
    admin_client: ApiClient, db: Session, clock: FakeClock
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now)

    response = admin_client.post(
        f"/api/admin/users/{alice.id}/reset-password", json={"temporary_password": "short"}
    )

    assert response.status_code == 422
    assert error_code(response) == "password_policy"


def test_reset_password_clears_lockout(
    admin_client: ApiClient, app: FastAPI, db: Session, clock: FakeClock
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now)
    for _ in range(5):
        ApiClient(app).login("alice@example.com", OTHER_PASSWORD)

    admin_client.post(
        f"/api/admin/users/{alice.id}/reset-password", json={"temporary_password": TEMP_PASSWORD}
    )

    assert ApiClient(app).login("alice@example.com", TEMP_PASSWORD).status_code == 200


def test_unknown_user_actions_return_404(admin_client: ApiClient) -> None:
    for method, path, body in [
        ("PATCH", "/api/admin/users/999999", {"display_name": "x"}),
        ("PUT", "/api/admin/users/999999/roles", {"role_ids": []}),
        ("POST", "/api/admin/users/999999/deactivate", None),
        ("POST", "/api/admin/users/999999/reactivate", None),
        ("POST", "/api/admin/users/999999/reset-password", {"temporary_password": TEMP_PASSWORD}),
    ]:
        response = admin_client.request(method, path, json=body)
        assert response.status_code == 404, (method, path)
        assert error_code(response) == "not_found", (method, path)
