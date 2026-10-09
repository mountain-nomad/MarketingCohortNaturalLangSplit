"""Test helpers: fake clock, reference-data seed, ORM factories and a CSRF-aware API client."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cohortsplit.auth import passwords
from cohortsplit.auth.catalog import ADMIN_ROLE_NAME, PERMISSION_CATALOG
from cohortsplit.auth.models import Permission, Role, RoleExportColumn, RolePermission, User

STRONG_PASSWORD = "correct horse battery staple"
OTHER_PASSWORD = "another long passphrase 42"
THIRD_PASSWORD = "yet another long passphrase"

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now = self.now + timedelta(**delta)


def seed_reference_data(db: Session, now: datetime) -> None:
    """What migration 0002_auth seeds: the permission catalog and the Admin system role."""
    for perm in PERMISSION_CATALOG:
        db.add(Permission(key=perm.key, description=perm.description))
    db.add(
        Role(
            name=ADMIN_ROLE_NAME,
            description="Protected system role with every permission",
            is_system=True,
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()


def admin_role(db: Session) -> Role:
    return db.execute(select(Role).where(Role.is_system.is_(True))).scalar_one()


def make_role(
    db: Session,
    name: str,
    permissions: Iterable[str] = (),
    export_columns: Iterable[str] = (),
    *,
    now: datetime,
) -> Role:
    role = Role(name=name, description="", is_system=False, created_at=now, updated_at=now)
    db.add(role)
    db.flush()
    for key in permissions:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    for column in export_columns:
        db.add(RoleExportColumn(role_id=role.id, column_ref=column))
    db.commit()
    return role


def make_user(
    db: Session,
    email: str,
    *,
    now: datetime,
    password: str = STRONG_PASSWORD,
    roles: Iterable[Role] = (),
    must_change_password: bool = False,
    is_active: bool = True,
    display_name: str | None = None,
) -> User:
    user = User(
        email=email.lower(),
        display_name=display_name or email.split("@")[0],
        password_hash=passwords.hash_password(password),
        must_change_password=must_change_password,
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )
    user.roles = list(roles)
    db.add(user)
    db.commit()
    return user


class ApiClient:
    """One browser: its own cookie jar; sends X-CSRF-Token on unsafe methods after login."""

    def __init__(self, app: FastAPI, base_url: str = "http://testserver") -> None:
        self.http = TestClient(app, base_url=base_url)
        self.csrf_token: str | None = None

    def login(self, email: str, password: str = STRONG_PASSWORD) -> httpx.Response:
        response = self.http.post("/api/auth/login", json={"email": email, "password": password})
        if response.status_code == 200:
            self.csrf_token = response.json()["csrf_token"]
        return response

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", None) or {})
        if method.upper() in UNSAFE_METHODS and self.csrf_token and "X-CSRF-Token" not in headers:
            headers["X-CSRF-Token"] = self.csrf_token
        return self.http.request(method, path, headers=headers, **kwargs)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)


def error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = body["error"].get("code")
        return code if isinstance(code, str) else None
    return None
