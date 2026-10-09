"""Every semantic-context endpoint refuses unauthenticated callers and callers without its
permission (AC-29, AC-A23). Runs on SQLite and PostgreSQL."""

import re
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from cohortsplit.auth.catalog import GRANTABLE_PERMISSIONS
from tests.auth.conftest import engine  # noqa: F401  (both backends for this module)
from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.semantic.conftest import clean_semantic_tables
from tests.semantic.helpers import (
    CANONICAL_USER,
    client_with,
    first_pending,
    seed_crawl,
)
from tests.semantic.routes import SEMANTIC_GATED

IDS = [f"{m} {p}" for m, p in SEMANTIC_GATED]
UNSAFE = [k for k in SEMANTIC_GATED if k[0] != "GET"]


@pytest.fixture(autouse=True)
def _clean_semantic_rows(engine: Engine) -> Iterator[None]:  # noqa: F811
    """Semantic rows reference users; remove them before the auth cleanup deletes users."""
    yield
    if engine.dialect.name == "postgresql":
        clean_semantic_tables(engine)


def concrete(path: str, key: str = "customer_id", use_case_id: int = 1) -> str:
    path = path.replace("{key}", key).replace("{use_case_id}", str(use_case_id))
    assert not re.search(r"\{[^}]+\}", path), path
    return path


@pytest.mark.parametrize(("method", "path"), list(SEMANTIC_GATED), ids=IDS)
def test_every_semantic_endpoint_refuses_unauthenticated(
    app: FastAPI, method: str, path: str
) -> None:
    response = ApiClient(app).request(method, concrete(path), json={})

    assert response.status_code == 401
    assert error_code(response) == "not_authenticated"


@pytest.mark.parametrize(("method", "path"), list(SEMANTIC_GATED), ids=IDS)
def test_every_semantic_endpoint_refuses_missing_permission(
    app: FastAPI, db: Session, clock: FakeClock, method: str, path: str
) -> None:
    required = SEMANTIC_GATED[(method, path)]
    client = client_with(app, db, clock, set(GRANTABLE_PERMISSIONS) - {required})

    response = client.request(method, concrete(path), json={})

    assert response.status_code == 403, response.text
    assert error_code(response) == "permission_denied"


@pytest.mark.parametrize(("method", "path"), UNSAFE, ids=[f"{m} {p}" for m, p in UNSAFE])
def test_semantic_mutations_require_csrf(admin_client: ApiClient, method: str, path: str) -> None:
    response = admin_client.http.request(method, concrete(path), json={})

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"


def test_marketer_without_semantic_permissions_refused_everywhere(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = client_with(app, db, clock, {"cohort.create", "cohort.export"})

    for method, path in SEMANTIC_GATED:
        response = client.request(method, concrete(path), json={})
        assert response.status_code == 403, (method, path)


def test_read_only_analyst_cannot_edit_or_review(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = client_with(app, db, clock, {"semantic_context.read"})

    for method, path in UNSAFE:
        response = client.request(method, concrete(path), json={})
        assert response.status_code == 403, (method, path)
        assert error_code(response) == "permission_denied"


@pytest.mark.parametrize(("method", "path"), list(SEMANTIC_GATED), ids=IDS)
def test_admin_passes_every_semantic_permission_check(
    engine: Engine,  # noqa: F811
    admin_client: ApiClient,
    method: str,
    path: str,
) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("needs the PostgreSQL crawler tables to resolve real ids")
    seed_crawl(engine)
    created = admin_client.post("/api/semantic/business-context", json=CANONICAL_USER)
    assert created.status_code == 201, created.text
    target = first_pending(engine)

    response = admin_client.request(
        method, concrete(path, use_case_id=target.id), json=_body(method, path)
    )

    # 404 would mean the route (or the seeded target) is missing.
    assert response.status_code not in (401, 403, 404), response.text


def _body(method: str, path: str) -> dict[str, object]:
    if path.endswith("/business-context/{key}") and method == "PUT":
        return {k: v for k, v in CANONICAL_USER.items() if k != "key"}
    return {}
