"""Audit of semantic-context edits and use-case review decisions (FR-A5).

Edits and decisions are sensitive: they change what every cohort means, so if the audit
event cannot be written the change is refused (fail closed)."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.auth.conftest import AuditReader
from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.semantic.helpers import (
    PURCHASE,
    SEMANTIC_PERMISSIONS,
    client_with,
    first_pending,
    seed_crawl,
    use_case_status,
)

ENTRIES = "/api/semantic/business-context"


@pytest.fixture
def analyst(app: FastAPI, db: Session, clock: FakeClock) -> ApiClient:
    return client_with(app, db, clock, SEMANTIC_PERMISSIONS)


@pytest.fixture
def crawled(engine: Engine) -> Iterator[int]:
    yield seed_crawl(engine)


def only(audit_events: AuditReader, action: str) -> Any:
    [event] = audit_events.of(action)
    return event


def test_business_context_edits_audited_with_before_and_after(
    analyst: ApiClient, crawled: int, audit_events: AuditReader
) -> None:
    created = analyst.post(ENTRIES, json=PURCHASE)
    assert created.status_code == 201
    changed = {k: v for k, v in PURCHASE.items() if k != "key"} | {"synonyms": ["bought"]}
    assert analyst.put(f"{ENTRIES}/purchase", json=changed).status_code == 200
    assert analyst.delete(f"{ENTRIES}/purchase").status_code == 204

    create = only(audit_events, "semantic_context.create")
    update = only(audit_events, "semantic_context.update")
    delete = only(audit_events, "semantic_context.delete")
    for event in (create, update, delete):
        assert (event.actor_type, event.outcome) == ("user", "success")
        assert (event.target_type, event.target_id) == ("business_context", "purchase")
        assert event.actor_user_id is not None
        assert len(event.metadata_["semantic_version_after"]) == 64
        assert event.request_id
    assert create.metadata_["after"]["definition"] == PURCHASE["definition"]
    assert create.metadata_["before"] is None
    assert update.metadata_["before"]["synonyms"] == ["bought", "purchased"]
    assert update.metadata_["after"]["synonyms"] == ["bought"]
    assert delete.metadata_["after"] is None
    assert update.metadata_["semantic_version_before"] == create.metadata_["semantic_version_after"]


@pytest.mark.parametrize("action", ["confirm", "reject"])
def test_review_decisions_audited(
    analyst: ApiClient, engine: Engine, crawled: int, audit_events: AuditReader, action: str
) -> None:
    target = first_pending(engine)

    assert analyst.post(f"/api/semantic/use-cases/{target.id}/{action}").status_code == 200

    event = only(audit_events, f"use_case.{action}")
    assert (event.target_type, event.target_id) == ("example_use_case", str(target.id))
    assert event.outcome == "success"
    assert event.metadata_["from_status"] == "pending_review"
    assert event.metadata_["to_status"] == ("confirmed" if action == "confirm" else "rejected")
    assert event.metadata_["origin"] == "generated"


def test_edit_decision_audited_with_spec_change(
    analyst: ApiClient, engine: Engine, crawled: int, audit_events: AuditReader
) -> None:
    target = first_pending(engine)
    body = {"nl_request": "Users with a paid order", "spec": target.spec}

    assert analyst.put(f"/api/semantic/use-cases/{target.id}", json=body).status_code == 200

    event = only(audit_events, "use_case.edit")
    assert event.metadata_["from_status"] == "pending_review"
    assert event.metadata_["to_status"] == "confirmed"
    assert event.metadata_["origin_before"] == "generated"
    assert event.metadata_["before"]["nl_request"] == target.nl_request
    assert event.metadata_["after"]["nl_request"] == "Users with a paid order"


def test_denied_attempts_are_audited(
    app: FastAPI, db: Session, clock: FakeClock, crawled: int, audit_events: AuditReader
) -> None:
    reader = client_with(app, db, clock, {"semantic_context.read"}, email="reader@example.com")

    assert reader.post(ENTRIES, json=PURCHASE).status_code == 403
    assert reader.post("/api/semantic/use-cases/1/confirm").status_code == 403

    denied = audit_events.of("access.denied")
    assert [(e.outcome, e.metadata_["permission"]) for e in denied] == [
        ("denied", "semantic_context.edit"),
        ("denied", "use_case.review"),
    ]
    assert audit_events.of("semantic_context.create") == []


def test_business_context_edit_fails_closed_without_audit(
    analyst: ApiClient, crawled: int, engine: Engine, audit_store_down: None
) -> None:
    response = analyst.post(ENTRIES, json=PURCHASE)

    assert response.status_code == 503
    assert error_code(response) == "audit_unavailable"

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM business_context_entries")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM semantic_versions")).scalar() == 0


def test_review_decision_fails_closed_without_audit(
    analyst: ApiClient, engine: Engine, crawled: int, request: pytest.FixtureRequest
) -> None:
    target = first_pending(engine)
    request.getfixturevalue("audit_store_down")

    response = analyst.post(f"/api/semantic/use-cases/{target.id}/confirm")

    assert response.status_code == 503
    assert error_code(response) == "audit_unavailable"
    assert use_case_status(engine, target.id) == "pending_review"


def test_audit_metadata_never_contains_sample_values_or_secrets(
    analyst: ApiClient, crawled: int, audit_events: AuditReader
) -> None:
    assert analyst.post(ENTRIES, json=PURCHASE).status_code == 201

    for event in audit_events.all():
        blob = str(event.metadata_).lower()
        assert "password" not in blob
        assert "csrf" not in blob
