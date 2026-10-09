"""Business-context editing in the admin page (FR-3): typed entries validated against the
latest crawl; unknown tables/columns refused."""

from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.semantic.helpers import (
    CANONICAL_USER,
    PURCHASE,
    client_with,
    seed_crawl,
    shop_without,
)

PATH = "/api/semantic/business-context"


@pytest.fixture
def editor(app: FastAPI, db: Session, clock: FakeClock) -> ApiClient:
    return client_with(app, db, clock, {"semantic_context.read", "semantic_context.edit"})


@pytest.fixture
def crawled(engine: Engine) -> int:
    return seed_crawl(engine)


def without_key(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k != "key"}


def listed(client: ApiClient) -> dict[str, dict[str, Any]]:
    response = client.get(PATH)
    assert response.status_code == 200, response.text
    return {item["key"]: item for item in response.json()["items"]}


def test_create_then_list_entry(editor: ApiClient, crawled: int) -> None:
    response = editor.post(PATH, json=PURCHASE)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["key"] == "purchase"
    assert body["kind"] == "metric"
    assert body["synonyms"] == ["bought", "purchased"]
    assert body["definition"] == PURCHASE["definition"]
    assert body["missing_references"] == []
    assert body["updated_by"]["display_name"] == "Analyst"
    assert listed(editor)["purchase"]["definition"] == PURCHASE["definition"]


def test_entries_listed_in_key_order(editor: ApiClient, crawled: int) -> None:
    assert editor.post(PATH, json=PURCHASE).status_code == 201
    assert editor.post(PATH, json=CANONICAL_USER).status_code == 201

    assert list(listed(editor)) == ["customer_id", "purchase"]


@pytest.mark.parametrize(
    ("definition", "problems"),
    [
        (
            {"kind": "term", "table": "public.product_likes"},
            [{"reference": "public.product_likes", "problem": "unknown table"}],
        ),
        (
            {
                "kind": "metric",
                "table": "public.orders",
                "filters": [{"column": "public.orders.state", "operator": "=", "value": "paid"}],
            },
            [{"reference": "public.orders.state", "problem": "unknown column"}],
        ),
        (
            {"kind": "canonical_user_id", "column": "public.users.email"},
            [
                {
                    "reference": "public.users.email",
                    "problem": "not the single-column primary key of its table",
                }
            ],
        ),
    ],
    ids=["unknown-table", "unknown-column", "canonical-not-pk"],
)
def test_unknown_references_refused(
    editor: ApiClient, crawled: int, definition: dict[str, Any], problems: list[Any]
) -> None:
    response = editor.post(PATH, json={"key": "thing", "definition": definition})

    assert response.status_code == 422, response.text
    assert error_code(response) == "invalid_reference"
    assert response.json()["error"]["problems"] == problems
    assert listed(editor) == {}


def test_writes_with_references_refused_before_any_crawl(editor: ApiClient) -> None:
    response = editor.post(PATH, json=PURCHASE)

    assert response.status_code == 409
    assert error_code(response) == "schema_inventory_unavailable"
    assert listed(editor) == {}


def test_entry_without_references_allowed_before_any_crawl(editor: ApiClient) -> None:
    recently = {"key": "recently", "definition": {"kind": "time_window", "last_days": 30}}

    assert editor.post(PATH, json=recently).status_code == 201


def test_malformed_entry_is_a_validation_error(editor: ApiClient, crawled: int) -> None:
    response = editor.post(PATH, json={**PURCHASE, "key": "Not A Key"})

    assert response.status_code == 422
    assert error_code(response) == "validation_error"


def test_duplicate_key_refused(editor: ApiClient, crawled: int) -> None:
    assert editor.post(PATH, json=PURCHASE).status_code == 201

    response = editor.post(PATH, json={**PURCHASE, "synonyms": []})

    assert response.status_code == 409
    assert error_code(response) == "business_context_key_taken"


@pytest.mark.parametrize(
    "synonyms", [["Bought"], ["purchase"]], ids=["same-synonym", "synonym-equals-key"]
)
def test_terms_resolve_to_at_most_one_entry(
    editor: ApiClient, crawled: int, synonyms: list[str]
) -> None:
    assert editor.post(PATH, json=PURCHASE).status_code == 201
    other = {
        "key": "paid_order",
        "synonyms": synonyms,
        "definition": {"kind": "term", "table": "public.orders"},
    }

    response = editor.post(PATH, json=other)

    assert response.status_code == 409
    assert error_code(response) == "term_conflict"
    assert response.json()["error"]["term"] == synonyms[0].lower()


def test_only_one_canonical_user_id(editor: ApiClient, crawled: int) -> None:
    assert editor.post(PATH, json=CANONICAL_USER).status_code == 201
    second = {**CANONICAL_USER, "key": "user_key", "synonyms": []}

    response = editor.post(PATH, json=second)

    assert response.status_code == 409
    assert error_code(response) == "canonical_user_id_exists"


def test_update_replaces_entry_and_keeps_key(
    editor: ApiClient, crawled: int, clock: FakeClock
) -> None:
    created = editor.post(PATH, json=PURCHASE).json()
    clock.advance(minutes=5)
    changed = without_key(PURCHASE)
    changed["definition"] = {
        **PURCHASE["definition"],
        "filters": [{"column": "public.orders.status", "operator": "=", "value": "delivered"}],
    }
    changed["synonyms"] = ["bought"]

    response = editor.put(f"{PATH}/purchase", json=changed)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"] == "purchase"
    assert body["synonyms"] == ["bought"]
    assert body["definition"]["filters"][0]["value"] == "delivered"
    assert body["updated_at"] > created["updated_at"]
    assert listed(editor)["purchase"]["definition"]["filters"][0]["value"] == "delivered"


def test_update_cannot_change_the_key(editor: ApiClient, crawled: int) -> None:
    assert editor.post(PATH, json=PURCHASE).status_code == 201

    response = editor.put(f"{PATH}/purchase", json={**PURCHASE, "key": "sale"})

    assert response.status_code == 422
    assert set(listed(editor)) == {"purchase"}


def test_update_keeping_own_synonyms_is_not_a_conflict(editor: ApiClient, crawled: int) -> None:
    assert editor.post(PATH, json=PURCHASE).status_code == 201

    response = editor.put(f"{PATH}/purchase", json=without_key(PURCHASE))

    assert response.status_code == 200, response.text


def test_update_validates_references(editor: ApiClient, crawled: int) -> None:
    assert editor.post(PATH, json=PURCHASE).status_code == 201
    broken = without_key(PURCHASE)
    broken["definition"] = {"kind": "term", "column": "public.orders.nope"}

    response = editor.put(f"{PATH}/purchase", json=broken)

    assert response.status_code == 422
    assert error_code(response) == "invalid_reference"
    assert listed(editor)["purchase"]["kind"] == "metric"


def test_delete_entry(editor: ApiClient, crawled: int) -> None:
    assert editor.post(PATH, json=PURCHASE).status_code == 201

    assert editor.delete(f"{PATH}/purchase").status_code == 204
    assert listed(editor) == {}
    assert editor.delete(f"{PATH}/purchase").status_code == 404


def test_update_unknown_entry_is_404(editor: ApiClient, crawled: int) -> None:
    response = editor.put(f"{PATH}/nothing", json=without_key(PURCHASE))

    assert response.status_code == 404
    assert error_code(response) == "not_found"


def test_entry_marked_when_schema_change_removes_a_reference(
    editor: ApiClient, engine: Engine, crawled: int
) -> None:
    assert editor.post(PATH, json=PURCHASE).status_code == 201

    seed_crawl(engine, shop_without("orders", "grand_total"))

    item = listed(editor)["purchase"]
    assert item["missing_references"] == [
        {"reference": "public.orders.grand_total", "problem": "unknown column"}
    ]
    assert item["definition"] == PURCHASE["definition"]  # never silently changed


def test_reader_sees_entries_without_edit_rights(
    app: FastAPI, db: Session, clock: FakeClock, editor: ApiClient, crawled: int
) -> None:
    assert editor.post(PATH, json=PURCHASE).status_code == 201
    reader = client_with(app, db, clock, {"semantic_context.read"}, email="reader@example.com")

    assert set(listed(reader)) == {"purchase"}
    assert reader.post(PATH, json=CANONICAL_USER).status_code == 403
