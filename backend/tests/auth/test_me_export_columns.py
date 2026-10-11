"""GET /api/me lists the extra columns the caller may export right now (BR-A3), so the split
step offers only grantable columns. UX only: every download re-checks (FR-A4)."""

from fastapi import FastAPI
from sqlalchemy.orm import Session

from tests.auth.helpers import ApiClient, FakeClock, admin_role, make_role, make_user

PHONE = "public.users.phone"
EMAIL = "public.users.email"


def login(
    app: FastAPI, db: Session, clock: FakeClock, permissions: list[str], columns: list[str]
) -> ApiClient:
    role = make_role(db, "Exporter", permissions, columns, now=clock.now)
    make_user(db, "exp@example.com", now=clock.now, roles=[role])
    client = ApiClient(app)
    assert client.login("exp@example.com").status_code == 200
    return client


def test_me_lists_granted_export_columns(app: FastAPI, db: Session, clock: FakeClock) -> None:
    client = login(app, db, clock, ["cohort.create", "cohort.export"], [PHONE, EMAIL])

    assert client.get("/api/me").json()["export_columns"] == [EMAIL, PHONE]


def test_grants_without_cohort_export_confer_nothing(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = login(app, db, clock, ["cohort.create"], [PHONE])

    assert client.get("/api/me").json()["export_columns"] == []


def test_admin_has_no_implicit_column_grants(app: FastAPI, db: Session, clock: FakeClock) -> None:
    make_user(db, "root@example.com", now=clock.now, roles=[admin_role(db)])
    client = ApiClient(app)
    client.login("root@example.com")

    assert client.get("/api/me").json()["export_columns"] == []
