"""Use-case review queue (FR-2/FR-3): confirm, edit (becomes human), reject, re-review."""

from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.semantic.helpers import (
    client_with,
    first_pending,
    seed_crawl,
    set_status,
    shop_without,
    spec_with_filter,
    use_case_by_template,
    use_case_status,
    use_cases,
)

PATH = "/api/semantic/use-cases"


@pytest.fixture
def reviewer(app: FastAPI, db: Session, clock: FakeClock) -> ApiClient:
    return client_with(app, db, clock, {"semantic_context.read", "use_case.review"})


@pytest.fixture
def crawled(engine: Engine) -> int:
    return seed_crawl(engine)


def queue(client: ApiClient, **query: str) -> list[dict[str, Any]]:
    response = client.get(PATH, params=query)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def test_queue_lists_generated_use_cases_with_status(
    reviewer: ApiClient, engine: Engine, crawled: int
) -> None:
    items = queue(reviewer)

    assert len(items) == len(use_cases(engine)) > 0
    first = items[0]
    assert set(first) >= {
        "id",
        "key",
        "origin",
        "status",
        "nl_request",
        "spec",
        "spec_version",
        "template_key",
        "rewritten_from",
        "generation_note",
        "review_note",
        "referenced_columns",
        "reviewed_by",
        "reviewed_at",
        "updated_at",
    }
    assert {i["status"] for i in items} == {"pending_review"}
    assert {i["origin"] for i in items} == {"generated"}


def test_queue_filters_by_status(reviewer: ApiClient, engine: Engine, crawled: int) -> None:
    target = first_pending(engine)
    set_status(engine, target.id, "needs_rereview", "column gone")

    flagged = queue(reviewer, status="needs_rereview")

    assert [(i["id"], i["review_note"]) for i in flagged] == [(target.id, "column gone")]
    assert reviewer.get(PATH, params={"status": "approved"}).status_code == 422


def test_confirm_records_reviewer(
    reviewer: ApiClient, engine: Engine, crawled: int, clock: FakeClock
) -> None:
    target = first_pending(engine)

    response = reviewer.post(f"{PATH}/{target.id}/confirm")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["origin"] == "generated"
    assert body["reviewed_by"]["display_name"] == "Analyst"
    assert body["reviewed_at"].startswith(clock.now.isoformat()[:16])
    assert use_case_status(engine, target.id) == "confirmed"


def test_reject_with_note(reviewer: ApiClient, engine: Engine, crawled: int) -> None:
    target = first_pending(engine)

    response = reviewer.post(f"{PATH}/{target.id}/reject", json={"note": "Not a real campaign"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"
    assert response.json()["review_note"] == "Not a real campaign"


def test_reject_without_body(reviewer: ApiClient, engine: Engine, crawled: int) -> None:
    target = first_pending(engine)

    response = reviewer.post(f"{PATH}/{target.id}/reject")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"


def test_reviewer_can_change_their_mind(reviewer: ApiClient, engine: Engine, crawled: int) -> None:
    target = first_pending(engine)

    assert reviewer.post(f"{PATH}/{target.id}/reject").status_code == 200
    assert reviewer.post(f"{PATH}/{target.id}/confirm").status_code == 200
    assert reviewer.post(f"{PATH}/{target.id}/reject").status_code == 200
    assert use_case_status(engine, target.id) == "rejected"


@pytest.mark.parametrize(
    ("status", "action"),
    [("confirmed", "confirm"), ("rejected", "reject")],
)
def test_repeating_a_decision_is_an_invalid_transition(
    reviewer: ApiClient, engine: Engine, crawled: int, status: str, action: str
) -> None:
    target = first_pending(engine)
    set_status(engine, target.id, status)

    response = reviewer.post(f"{PATH}/{target.id}/{action}")

    assert response.status_code == 409
    assert error_code(response) == "invalid_transition"


def test_needs_rereview_can_be_confirmed_when_references_resolve(
    reviewer: ApiClient, engine: Engine, crawled: int
) -> None:
    target = first_pending(engine)
    set_status(engine, target.id, "needs_rereview", "flagged")

    response = reviewer.post(f"{PATH}/{target.id}/confirm")

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"


def test_confirm_refused_when_spec_references_missing_columns(
    reviewer: ApiClient, engine: Engine, crawled: int
) -> None:
    target = use_case_by_template(engine, "orders_with_status")
    # The warehouse lost orders.status: confirming the stale spec would feed the LLM a lie.

    set_status(engine, target.id, "needs_rereview", "flagged")
    seed_crawl(engine, shop_without("orders", "status"))

    response = reviewer.post(f"{PATH}/{target.id}/confirm")

    assert response.status_code == 422
    assert error_code(response) == "invalid_reference"
    assert {"reference": "public.orders.status", "problem": "unknown column"} in response.json()[
        "error"
    ]["problems"]
    assert use_case_status(engine, target.id) == "needs_rereview"


def test_edit_makes_use_case_human_and_confirmed(
    reviewer: ApiClient, engine: Engine, crawled: int
) -> None:
    target = first_pending(engine)
    spec = spec_with_filter("public.orders.status", "delivered")

    response = reviewer.put(
        f"{PATH}/{target.id}",
        json={"nl_request": "Users with a delivered order", "spec": spec},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["origin"] == "human"
    assert body["status"] == "confirmed"
    assert body["nl_request"] == "Users with a delivered order"
    assert body["spec"] == spec
    assert body["spec_version"] == "draft-0"
    assert body["key"] == target.use_case_key
    assert set(body["referenced_columns"]) == {
        "public.users.user_id",
        "public.orders.user_id",
        "public.orders.status",
    }


def test_edit_fixes_a_needs_rereview_use_case(
    reviewer: ApiClient, engine: Engine, crawled: int
) -> None:
    target = first_pending(engine)
    set_status(engine, target.id, "needs_rereview", "flagged")

    response = reviewer.put(
        f"{PATH}/{target.id}",
        json={
            "nl_request": "Users with a paid order",
            "spec": spec_with_filter("public.orders.status"),
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert response.json()["review_note"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"nl_request": "", "spec": spec_with_filter("public.orders.status")},
        {"nl_request": "x", "spec": {"spec_version": "draft-0"}},
        {"nl_request": "x", "spec": {**spec_with_filter("public.orders.status"), "sql": "x"}},
        {
            "nl_request": "x",
            "spec": {**spec_with_filter("public.orders.status"), "spec_version": "v9"},
        },
    ],
    ids=["blank-request", "incomplete-spec", "extra-field", "unknown-spec-version"],
)
def test_edit_with_invalid_spec_refused(
    reviewer: ApiClient, engine: Engine, crawled: int, body: dict[str, Any]
) -> None:
    target = first_pending(engine)

    response = reviewer.put(f"{PATH}/{target.id}", json=body)

    assert response.status_code == 422
    assert use_case_status(engine, target.id) == "pending_review"


def test_edit_with_unknown_column_refused(
    reviewer: ApiClient, engine: Engine, crawled: int
) -> None:
    target = first_pending(engine)

    response = reviewer.put(
        f"{PATH}/{target.id}",
        json={"nl_request": "x", "spec": spec_with_filter("public.orders.mood")},
    )

    assert response.status_code == 422
    assert error_code(response) == "invalid_reference"
    assert use_case_status(engine, target.id) == "pending_review"


@pytest.mark.parametrize(
    ("method", "suffix"), [("POST", "/confirm"), ("POST", "/reject"), ("PUT", "")]
)
def test_unknown_use_case_is_404(
    reviewer: ApiClient, crawled: int, method: str, suffix: str
) -> None:
    body = {"nl_request": "x", "spec": spec_with_filter("public.orders.status")}

    response = reviewer.request(method, f"{PATH}/999999{suffix}", json=body)

    assert response.status_code == 404
    assert error_code(response) == "not_found"


def test_edited_use_case_survives_recrawl_and_its_template_is_not_regenerated(
    reviewer: ApiClient, engine: Engine, crawled: int
) -> None:
    target = first_pending(engine)
    template = target.template_key
    assert template is not None
    edited = reviewer.put(
        f"{PATH}/{target.id}",
        json={"nl_request": "Edited by a human", "spec": spec_with_filter("public.orders.status")},
    )
    assert edited.status_code == 200

    seed_crawl(engine)

    same_template = [u for u in use_cases(engine) if u.template_key == template]
    assert [(u.id, u.origin, u.status, u.nl_request) for u in same_template] == [
        (target.id, "human", "confirmed", "Edited by a human")
    ]
