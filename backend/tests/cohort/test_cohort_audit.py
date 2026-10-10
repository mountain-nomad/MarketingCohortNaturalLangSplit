"""Audit of interpretations and cohort runs (FR-A5, docs/product/authentication.md).

A cohort run records the NL request, spec hash, SQL hash, semantic version and row count;
it is sensitive (fail closed). Events never contain result rows or export values.
"""

import json
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cohortsplit.cohort.models import PreparedCohortRow
from cohortsplit.warehouse.errors import QueryTimeoutError
from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.cohort.helpers import (
    AC2_REQUEST,
    INTERPRET,
    PREVIEW,
    FakeExecutor,
    confirm,
    install_environment,
    interpret_ok,
    marketer,
)

PHONE = "public.users.phone"


@pytest.fixture
def client(app: FastAPI, db: Session, clock: FakeClock) -> ApiClient:
    return marketer(
        app, db, clock, permissions=("cohort.create", "cohort.export"), export_columns=[PHONE]
    )


def test_interpretation_is_audited(app: FastAPI, client: ApiClient, audit_events: Any) -> None:
    env = install_environment(app)

    interpreted = interpret_ok(client)

    [event] = audit_events.of("cohort.interpret")
    assert event.outcome == "success"
    assert event.actor_type == "user"
    assert event.target_type == "cohort_interpretation"
    assert event.target_id == interpreted["interpretation_id"]
    assert event.metadata_["nl_request"] == AC2_REQUEST
    assert event.metadata_["kind"] == "interpretation"
    assert event.metadata_["spec_hash"] == interpreted["spec_hash"]
    assert event.metadata_["semantic_version"] == env.snapshot().semantic_version


def test_rejections_are_audited_as_denied(
    app: FastAPI, client: ApiClient, audit_events: Any
) -> None:
    install_environment(app)

    client.post(INTERPRET, json={"nl_request": "Delete inactive users"})

    [event] = audit_events.of("cohort.interpret")
    assert event.outcome == "denied"
    assert event.metadata_["kind"] == "rejection"
    assert event.metadata_["reason"] == "mutation"
    assert event.metadata_["nl_request"] == "Delete inactive users"


def test_cohort_run_is_audited_with_hashes_version_and_row_count(
    app: FastAPI, client: ApiClient, audit_events: Any
) -> None:
    install_environment(
        app, executor=FakeExecutor(members=[1, 2], columns={PHONE: {1: "+4915112345", 2: None}})
    )
    interpreted = interpret_ok(client)

    body = client.post(PREVIEW, json=confirm(interpreted, export_columns=[PHONE])).json()

    [event] = audit_events.of("cohort.run")
    assert event.outcome == "success"
    assert event.target_type == "prepared_cohort"
    assert event.target_id == body["prepared_cohort_id"]
    metadata = event.metadata_
    assert metadata["nl_request"] == AC2_REQUEST
    assert metadata["spec_hash"] == interpreted["spec_hash"]
    assert metadata["sql_hash"] == body["sql_hash"]
    assert metadata["semantic_version"] == interpreted["semantic_version"]
    assert metadata["row_count"] == 2
    assert metadata["export_columns"] == [PHONE]
    # No result rows or export values in the audit log (AC-A20).
    assert "+4915112345" not in json.dumps(metadata)


def test_failed_runs_are_audited_as_errors(
    app: FastAPI, client: ApiClient, audit_events: Any
) -> None:
    install_environment(app, executor=FakeExecutor(error=QueryTimeoutError(30)))

    client.post(PREVIEW, json=confirm(interpret_ok(client)))

    [event] = audit_events.of("cohort.run")
    assert event.outcome == "error"
    assert event.metadata_["error"] == "query_timeout"
    assert event.metadata_["spec_hash"]


def test_cohort_run_fails_closed_without_audit(
    app: FastAPI,
    client: ApiClient,
    db: Session,
    request: pytest.FixtureRequest,
) -> None:
    install_environment(app)
    interpreted = interpret_ok(client)
    request.getfixturevalue("audit_store_down")

    response = client.post(PREVIEW, json=confirm(interpreted))

    assert response.status_code == 503
    assert error_code(response) == "audit_unavailable"
    assert "total_count" not in response.text
    assert db.scalar(select(func.count()).select_from(PreparedCohortRow)) == 0


def test_permission_denials_are_audited(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: Any
) -> None:
    install_environment(app)
    outsider = marketer(app, db, clock, email="outsider@example.com", permissions=("audit.read",))

    assert outsider.post(INTERPRET, json={"nl_request": "x"}).status_code == 403
    assert outsider.post(PREVIEW, json={}).status_code == 403

    denied = audit_events.of("access.denied")
    assert [e.metadata_["permission"] for e in denied] == ["cohort.create", "cohort.create"]
