"""Export authorization gate and column export grants (FR-A4, AC-A9, AC-A15..AC-A17, AC-A21).

There is no export endpoint yet (feature experiment-split). These tests drive a test-only
route built from the public interfaces that feature must use.
"""

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from cohortsplit.auth.dependencies import require_permission
from cohortsplit.auth.models import Role, User
from cohortsplit.auth.policy import PolicyService
from tests.auth.conftest import AuditReader
from tests.auth.helpers import (
    EXPORT_TEST_PATH,
    ApiClient,
    FakeClock,
    error_code,
    export_test_router,
    make_role,
    make_user,
)

PHONE = "public.users.phone"
EMAIL = "public.users.email"


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    app.include_router(export_test_router())
    return app


@pytest.fixture
def marketer_role(db: Session, clock: FakeClock) -> Role:
    return make_role(db, "Marketer", ["cohort.create", "cohort.export"], now=clock.now)


@pytest.fixture
def alice(db: Session, clock: FakeClock, marketer_role: Role) -> User:
    return make_user(db, "alice@example.com", now=clock.now, roles=[marketer_role])


@pytest.fixture
def alice_client(app: FastAPI, alice: User) -> ApiClient:
    client = ApiClient(app)
    assert client.login("alice@example.com").status_code == 200
    return client


def export(client: ApiClient, *columns: str, redownload: bool = False):  # type: ignore[no-untyped-def]
    return client.post(
        EXPORT_TEST_PATH,
        json={"run_id": "run-42", "columns": list(columns), "redownload": redownload},
    )


def test_canonical_user_id_exportable_with_cohort_export_alone(
    alice_client: ApiClient, alice: User, audit_events: AuditReader
) -> None:
    response = export(alice_client)

    assert response.status_code == 200
    assert response.json() == {"canonical_user_id": True, "columns": []}
    [event] = audit_events.of("cohort.export")
    assert event.outcome == "success"
    assert event.actor_user_id == alice.id
    assert (event.target_type, event.target_id) == ("cohort_run", "run-42")
    assert event.metadata_["columns"] == []
    assert event.metadata_["row_counts"] == {"test": 3, "control": 2}


def test_export_of_ungranted_column_refused_and_denial_audited(
    alice_client: ApiClient, audit_events: AuditReader
) -> None:
    response = export(alice_client, PHONE)

    assert response.status_code == 403
    assert error_code(response) == "export_column_denied"
    assert response.json()["error"]["columns"] == [PHONE]
    [event] = audit_events.of("cohort.export")
    assert event.outcome == "denied"
    assert event.metadata_["denied_columns"] == [PHONE]


def test_redownload_of_ungranted_column_refused_and_audited(
    alice_client: ApiClient, audit_events: AuditReader
) -> None:
    response = export(alice_client, PHONE, redownload=True)

    assert response.status_code == 403
    [event] = audit_events.of("cohort.redownload")
    assert event.outcome == "denied"
    assert audit_events.of("cohort.export") == []


def test_grant_then_revoke_column(
    alice_client: ApiClient, admin_client: ApiClient, marketer_role: Role
) -> None:
    assert export(alice_client, PHONE).status_code == 403

    granted = admin_client.patch(
        f"/api/admin/roles/{marketer_role.id}", json={"export_columns": [PHONE]}
    )
    assert granted.status_code == 200
    allowed = export(alice_client, PHONE)
    assert allowed.status_code == 200
    assert allowed.json()["columns"] == [PHONE]
    assert export(alice_client, PHONE, redownload=True).status_code == 200

    admin_client.patch(f"/api/admin/roles/{marketer_role.id}", json={"export_columns": []})

    assert export(alice_client, PHONE).status_code == 403
    assert export(alice_client, PHONE, redownload=True).status_code == 403


def test_grant_on_any_role_of_the_user_suffices(
    app: FastAPI, db: Session, clock: FakeClock, marketer_role: Role
) -> None:
    phone_grant = make_role(db, "Phone export", [], [PHONE], now=clock.now)
    make_user(db, "bob@example.com", now=clock.now, roles=[marketer_role, phone_grant])
    client = ApiClient(app)
    client.login("bob@example.com")

    assert export(client, PHONE).status_code == 200
    assert export(client, PHONE, EMAIL).status_code == 403


def test_column_grant_without_cohort_export_confers_nothing(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    grant_only = make_role(db, "Phone export", ["cohort.create"], [PHONE], now=clock.now)
    make_user(db, "carol@example.com", now=clock.now, roles=[grant_only])
    client = ApiClient(app)
    client.login("carol@example.com")

    response = export(client, PHONE)

    assert response.status_code == 403
    assert error_code(response) == "permission_denied"


def test_removing_cohort_export_from_role_refuses_next_export(
    alice_client: ApiClient, admin_client: ApiClient, marketer_role: Role
) -> None:
    assert export(alice_client).status_code == 200

    admin_client.patch(
        f"/api/admin/roles/{marketer_role.id}", json={"permissions": ["cohort.create"]}
    )

    response = export(alice_client)
    assert response.status_code == 403
    assert error_code(response) == "permission_denied"


def test_admin_has_no_implicit_column_grants(admin_client: ApiClient) -> None:
    assert export(admin_client).status_code == 200
    assert export(admin_client, PHONE).status_code == 403


def test_export_refused_when_audit_store_rejects_writes(
    alice_client: ApiClient, audit_store_down: None
) -> None:
    response = export(alice_client)

    assert response.status_code == 503
    assert error_code(response) == "audit_unavailable"


def test_denied_export_still_refused_when_audit_store_rejects_writes(
    alice_client: ApiClient, audit_store_down: None
) -> None:
    assert export(alice_client, PHONE).status_code in (403, 503)


def test_effective_export_columns_is_union_of_role_grants(
    app: FastAPI, db: Session, clock: FakeClock, marketer_role: Role
) -> None:
    extra = make_role(db, "Contact", [], [EMAIL, PHONE], now=clock.now)
    other = make_role(db, "Other", [], ["public.orders.total"], now=clock.now)
    user = make_user(db, "dan@example.com", now=clock.now, roles=[marketer_role, extra])
    make_user(db, "eve@example.com", now=clock.now, roles=[other])
    policy: PolicyService = app.state.auth.policy

    principal = policy.principal_for(db, user, session_id=0)
    columns = policy.effective_export_columns(db, principal)

    assert columns.canonical_user_id is True
    assert columns.granted_columns == frozenset({EMAIL, PHONE})
    assert columns.allows(PHONE)
    assert not columns.allows("public.orders.total")


def test_effective_export_columns_empty_without_cohort_export(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    grant_only = make_role(db, "Contact", ["cohort.create"], [PHONE], now=clock.now)
    user = make_user(db, "frank@example.com", now=clock.now, roles=[grant_only])
    policy: PolicyService = app.state.auth.policy

    columns = policy.effective_export_columns(db, policy.principal_for(db, user, session_id=0))

    assert columns.canonical_user_id is False
    assert columns.granted_columns == frozenset()
    assert not columns.allows(PHONE)


def test_require_permission_rejects_unknown_permission_names() -> None:
    with pytest.raises(ValueError, match="unknown permission"):
        require_permission("cohort.exprot")
