"""Dynamic roles, permissions and export-column grants (FR-A3, FR-A4, AC-A8..AC-A11, AC-A14)."""

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from cohortsplit.auth.catalog import (
    ADMIN_ONLY_PERMISSIONS,
    ALL_PERMISSIONS,
    GRANTABLE_PERMISSIONS,
)
from cohortsplit.auth.models import Role, User
from tests.auth.conftest import AuditReader
from tests.auth.helpers import (
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


def create_role(admin: ApiClient, **overrides: object):  # type: ignore[no-untyped-def]
    body: dict[str, object] = {
        "name": "Marketer",
        "description": "Push campaigns",
        "permissions": ["cohort.create", "cohort.export"],
        "export_columns": ["public.users.phone"],
    }
    body.update(overrides)
    return admin.post("/api/admin/roles", json=body)


def test_permission_catalog_marks_admin_only_permissions(admin_client: ApiClient) -> None:
    response = admin_client.get("/api/admin/permissions")

    assert response.status_code == 200
    items = {p["key"]: p for p in response.json()["items"]}
    assert set(items) == ALL_PERMISSIONS
    assert {k for k, p in items.items() if not p["grantable"]} == ADMIN_ONLY_PERMISSIONS
    assert all(p["description"] and p["area"] for p in items.values())


def test_list_roles_includes_protected_admin_with_every_permission(
    admin_client: ApiClient,
) -> None:
    roles = admin_client.get("/api/admin/roles").json()["items"]

    [admin] = [r for r in roles if r["is_system"]]
    assert admin["name"] == "Admin"
    assert set(admin["permissions"]) == ALL_PERMISSIONS


def test_create_role_with_permissions_and_export_columns(
    admin_client: ApiClient, admin_user: User, audit_events: AuditReader
) -> None:
    response = create_role(admin_client)

    assert response.status_code == 201
    role = response.json()
    assert role["name"] == "Marketer"
    assert role["description"] == "Push campaigns"
    assert role["is_system"] is False
    assert role["permissions"] == ["cohort.create", "cohort.export"]
    assert role["export_columns"] == ["public.users.phone"]
    assert role["member_ids"] == []
    [event] = audit_events.of("role.create")
    assert event.actor_user_id == admin_user.id
    assert event.target_id == str(role["id"])
    assert event.metadata_["permissions"] == ["cohort.create", "cohort.export"]
    assert event.metadata_["export_columns"] == ["public.users.phone"]


@pytest.mark.parametrize("permission", sorted(ADMIN_ONLY_PERMISSIONS))
def test_custom_role_with_admin_only_permission_refused(
    admin_client: ApiClient, permission: str
) -> None:
    created = create_role(admin_client, permissions=["cohort.create", permission])
    assert created.status_code == 422
    assert error_code(created) == "permission_not_grantable"

    role = create_role(admin_client, name="Plain", permissions=["cohort.create"]).json()
    updated = admin_client.patch(
        f"/api/admin/roles/{role['id']}", json={"permissions": ["cohort.create", permission]}
    )
    assert updated.status_code == 422
    assert error_code(updated) == "permission_not_grantable"

    names = [r["name"] for r in admin_client.get("/api/admin/roles").json()["items"]]
    assert "Marketer" not in names
    stored = admin_client.get(f"/api/admin/roles/{role['id']}").json()
    assert stored["permissions"] == ["cohort.create"]


def test_unknown_permission_refused(admin_client: ApiClient) -> None:
    response = create_role(admin_client, permissions=["cohort.delete_everything"])

    assert response.status_code == 422
    assert error_code(response) == "unknown_permission"


@pytest.mark.parametrize(
    "column",
    [
        "phone",
        "users.phone",
        "public.users.phone.extra",
        "public.users.",
        ".users.phone",
        "public.users.ph one",
        "public.users.phone;drop",
        "1public.users.phone",
        "",
    ],
)
def test_invalid_export_column_format_refused(admin_client: ApiClient, column: str) -> None:
    response = create_role(admin_client, export_columns=[column])

    assert response.status_code == 422
    assert error_code(response) == "invalid_column"


def test_export_columns_are_deduplicated_and_sorted(admin_client: ApiClient) -> None:
    response = create_role(
        admin_client,
        export_columns=["public.users.phone", "public.users.email", "public.users.phone"],
    )

    assert response.json()["export_columns"] == ["public.users.email", "public.users.phone"]


@pytest.mark.parametrize("name", ["Marketer", "MARKETER", " marketer "])
def test_role_name_unique_case_insensitive(admin_client: ApiClient, name: str) -> None:
    create_role(admin_client)

    response = create_role(admin_client, name=name)

    assert response.status_code == 409
    assert error_code(response) == "role_name_taken"


def test_role_cannot_be_named_like_the_admin_role(admin_client: ApiClient) -> None:
    response = create_role(admin_client, name="admin")

    assert response.status_code == 409
    assert error_code(response) == "role_name_taken"


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_role_name_length_validated(admin_client: ApiClient, name: str) -> None:
    assert create_role(admin_client, name=name).status_code == 422


def test_effective_permissions_are_union_of_roles(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    r1 = make_role(db, "R1", ["cohort.create"], now=clock.now)
    r2 = make_role(db, "R2", ["cohort.export"], now=clock.now)
    make_user(db, "both@example.com", now=clock.now, roles=[r1, r2])

    me = logged_in(app, "both@example.com").get("/api/me").json()

    assert me["permissions"] == ["cohort.create", "cohort.export"]
    assert [r["name"] for r in me["roles"]] == ["R1", "R2"]


def test_new_role_takes_effect_without_restart(
    admin_client: ApiClient, app: FastAPI, db: Session, clock: FakeClock
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now)
    alice_client = logged_in(app, "alice@example.com")
    assert alice_client.get("/api/audit/events").status_code == 403

    role = create_role(admin_client, name="Auditor", permissions=["audit.read"], export_columns=[])
    assigned = admin_client.put(
        f"/api/admin/roles/{role.json()['id']}/members", json={"user_ids": [alice.id]}
    )

    assert assigned.status_code == 200
    assert assigned.json()["member_ids"] == [alice.id]
    assert alice_client.get("/api/audit/events").status_code == 200


def test_permission_removed_from_role_takes_effect_on_next_request(
    admin_client: ApiClient, app: FastAPI, db: Session, clock: FakeClock
) -> None:
    role = make_role(db, "Auditor", ["audit.read"], now=clock.now)
    make_user(db, "alice@example.com", now=clock.now, roles=[role])
    alice_client = logged_in(app, "alice@example.com")
    assert alice_client.get("/api/audit/events").status_code == 200

    admin_client.patch(f"/api/admin/roles/{role.id}", json={"permissions": []})

    assert alice_client.get("/api/audit/events").status_code == 403


def test_role_update_is_audited_with_before_and_after(
    admin_client: ApiClient, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    role = make_role(db, "Marketer", ["cohort.create"], ["public.users.email"], now=clock.now)

    response = admin_client.patch(
        f"/api/admin/roles/{role.id}",
        json={
            "permissions": ["cohort.create", "cohort.export"],
            "export_columns": ["public.users.phone"],
        },
    )

    assert response.status_code == 200
    [event] = audit_events.of("role.update")
    assert event.metadata_["permissions"] == {
        "before": ["cohort.create"],
        "after": ["cohort.create", "cohort.export"],
    }
    assert event.metadata_["export_columns"] == {
        "before": ["public.users.email"],
        "after": ["public.users.phone"],
    }
    assert "name" not in event.metadata_


def test_role_rename_keeps_assignments_and_audits_old_and_new_name(
    admin_client: ApiClient, app: FastAPI, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    role = make_role(db, "Marketer", ["cohort.create"], now=clock.now)
    alice = make_user(db, "alice@example.com", now=clock.now, roles=[role])

    response = admin_client.patch(f"/api/admin/roles/{role.id}", json={"name": "KZ Marketing"})

    assert response.status_code == 200
    assert response.json()["member_ids"] == [alice.id]
    me = logged_in(app, "alice@example.com").get("/api/me").json()
    assert [r["name"] for r in me["roles"]] == ["KZ Marketing"]
    [event] = audit_events.of("role.update")
    assert event.metadata_["name"] == {"before": "Marketer", "after": "KZ Marketing"}


def test_rename_to_existing_name_refused(
    admin_client: ApiClient, db: Session, clock: FakeClock
) -> None:
    make_role(db, "Analyst", [], now=clock.now)
    role = make_role(db, "Marketer", [], now=clock.now)

    response = admin_client.patch(f"/api/admin/roles/{role.id}", json={"name": "analyst"})

    assert response.status_code == 409
    assert error_code(response) == "role_name_taken"


def test_admin_role_cannot_be_edited(
    admin_client: ApiClient, db: Session, audit_events: AuditReader
) -> None:
    admin = admin_role(db)

    for body in (
        {"name": "Superuser"},
        {"description": "changed"},
        {"permissions": ["cohort.create"]},
        {"export_columns": ["public.users.phone"]},
    ):
        response = admin_client.patch(f"/api/admin/roles/{admin.id}", json=body)
        assert response.status_code == 403
        assert error_code(response) == "system_role_protected"

    stored = admin_client.get(f"/api/admin/roles/{admin.id}").json()
    assert stored["name"] == "Admin"
    assert stored["export_columns"] == []
    # Refusals are audited (FR-A5 "denied actions are audited"); nothing succeeded.
    assert [e.outcome for e in audit_events.of("role.update")] == ["denied"] * 4


def test_admin_role_cannot_be_deleted(admin_client: ApiClient, db: Session) -> None:
    admin = admin_role(db)

    response = admin_client.delete(f"/api/admin/roles/{admin.id}?confirm=true")

    assert response.status_code == 403
    assert error_code(response) == "system_role_protected"
    assert admin_client.get("/api/me").json()["is_admin"] is True


def test_admin_role_members_are_not_managed_from_the_role_endpoint(
    admin_client: ApiClient, db: Session, clock: FakeClock
) -> None:
    alice = make_user(db, "alice@example.com", now=clock.now)

    response = admin_client.put(
        f"/api/admin/roles/{admin_role(db).id}/members", json={"user_ids": [alice.id]}
    )

    assert response.status_code == 403
    assert error_code(response) == "system_role_protected"


def test_delete_unassigned_role(
    admin_client: ApiClient, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    role = make_role(db, "Temp", ["cohort.create"], now=clock.now)

    response = admin_client.delete(f"/api/admin/roles/{role.id}")

    assert response.status_code == 204
    assert admin_client.get(f"/api/admin/roles/{role.id}").status_code == 404
    [event] = audit_events.of("role.delete")
    assert event.metadata_["name"] == "Temp"


def test_delete_assigned_role_requires_confirmation_and_revokes_immediately(
    admin_client: ApiClient, app: FastAPI, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    role = make_role(db, "Auditor", ["audit.read"], now=clock.now)
    alice = make_user(db, "alice@example.com", now=clock.now, roles=[role])
    alice_client = logged_in(app, "alice@example.com")

    unconfirmed = admin_client.delete(f"/api/admin/roles/{role.id}")
    assert unconfirmed.status_code == 409
    assert error_code(unconfirmed) == "confirmation_required"
    assert unconfirmed.json()["error"]["member_count"] == 1
    assert alice_client.get("/api/audit/events").status_code == 200

    confirmed = admin_client.delete(f"/api/admin/roles/{role.id}?confirm=true")

    assert confirmed.status_code == 204
    assert alice_client.get("/api/audit/events").status_code == 403
    [event] = audit_events.of("role.delete")
    assert event.metadata_["member_ids"] == [alice.id]
    assert event.metadata_["permissions"] == ["audit.read"]


def test_role_members_endpoint_sets_members_with_audit(
    admin_client: ApiClient, db: Session, clock: FakeClock, audit_events: AuditReader
) -> None:
    role = make_role(db, "Marketer", ["cohort.create"], now=clock.now)
    alice = make_user(db, "alice@example.com", now=clock.now, roles=[role])
    bob = make_user(db, "bob@example.com", now=clock.now)

    response = admin_client.put(f"/api/admin/roles/{role.id}/members", json={"user_ids": [bob.id]})

    assert response.status_code == 200
    assert response.json()["member_ids"] == [bob.id]
    assert [e.target_id for e in audit_events.of("role.assign")] == [str(bob.id)]
    assert [e.target_id for e in audit_events.of("role.unassign")] == [str(alice.id)]


def test_role_members_with_unknown_user_refused(
    admin_client: ApiClient, db: Session, clock: FakeClock
) -> None:
    role = make_role(db, "Marketer", ["cohort.create"], now=clock.now)

    response = admin_client.put(f"/api/admin/roles/{role.id}/members", json={"user_ids": [999999]})

    assert response.status_code == 422
    assert error_code(response) == "unknown_user"


def test_unknown_role_returns_404(admin_client: ApiClient) -> None:
    for response in (
        admin_client.get("/api/admin/roles/999999"),
        admin_client.patch("/api/admin/roles/999999", json={"name": "x"}),
        admin_client.delete("/api/admin/roles/999999"),
        admin_client.put("/api/admin/roles/999999/members", json={"user_ids": []}),
    ):
        assert response.status_code == 404
        assert error_code(response) == "not_found"


def test_client_supplied_role_and_permission_claims_are_ignored(
    app: FastAPI, db: Session, clock: FakeClock, admin_user: User
) -> None:
    role = make_role(db, "Marketer", ["cohort.create"], now=clock.now)
    make_user(db, "alice@example.com", now=clock.now, roles=[role])
    client = logged_in(app, "alice@example.com")
    claims = {
        "X-Roles": "Admin",
        "X-Permissions": "audit.read,user.read",
        "X-User-Id": str(admin_user.id),
        "X-Is-Admin": "true",
    }
    client.http.cookies.set("permissions", "audit.read")
    client.http.cookies.set("role", "Admin")

    audit = client.get("/api/audit/events?permissions=audit.read&is_admin=true", headers=claims)
    users = client.get("/api/admin/users", headers=claims)
    create = client.post(
        "/api/admin/roles",
        headers=claims,
        json={"name": "Escalate", "permissions": ["audit.read"], "is_admin": True},
    )
    me = client.get("/api/me", headers=claims).json()

    assert audit.status_code == 403
    assert users.status_code == 403
    assert create.status_code == 403
    assert me["permissions"] == ["cohort.create"]
    assert me["is_admin"] is False
    assert me["email"] == "alice@example.com"


def test_grantable_permissions_can_all_be_combined(admin_client: ApiClient) -> None:
    response = create_role(admin_client, permissions=sorted(GRANTABLE_PERMISSIONS))

    assert response.status_code == 201
    assert response.json()["permissions"] == sorted(GRANTABLE_PERMISSIONS)


def test_role_is_retrievable_by_id(admin_client: ApiClient, db: Session, clock: FakeClock) -> None:
    role: Role = make_role(db, "Analyst", ["semantic_context.read"], now=clock.now)

    body = admin_client.get(f"/api/admin/roles/{role.id}").json()

    assert body["name"] == "Analyst"
    assert body["permissions"] == ["semantic_context.read"]
