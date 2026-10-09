"""Business-context entry shape (FR-3, ruling S1): typed, validated, no SQL."""

from typing import Any

import pytest
from pydantic import ValidationError

from cohortsplit.semantic.context import BusinessContextIn, definition_references

PURCHASE: dict[str, Any] = {
    "key": "purchase",
    "synonyms": ["bought", "purchased"],
    "description": "An order the customer actually paid for.",
    "definition": {
        "kind": "metric",
        "table": "public.orders",
        "filters": [
            {
                "column": "public.orders.status",
                "operator": "in",
                "value": ["paid", "shipped", "delivered"],
            }
        ],
        "value_column": "public.orders.grand_total",
        "time_column": "public.orders.ordered_at",
    },
}


def entry(**overrides: Any) -> dict[str, Any]:
    return {**PURCHASE, **overrides}


def definition(**fields: Any) -> dict[str, Any]:
    return entry(definition=fields)


def test_metric_entry_parses_with_typed_filters() -> None:
    parsed = BusinessContextIn.model_validate(PURCHASE)

    assert parsed.key == "purchase"
    assert parsed.definition.kind == "metric"
    assert parsed.definition.table == "public.orders"
    [status] = parsed.definition.filters
    assert (status.column, status.operator, status.value) == (
        "public.orders.status",
        "in",
        ("paid", "shipped", "delivered"),
    )


@pytest.mark.parametrize(
    "payload",
    [
        definition(kind="term", table="public.users"),
        definition(kind="term", column="public.addresses.country_code"),
        definition(
            kind="status_semantics",
            column="public.carts.status",
            meanings={"abandoned": "Left without checkout", "converted": "Became an order"},
        ),
        definition(kind="time_window", last_days=30),
        definition(kind="time_window", last_days=90, column="public.orders.ordered_at"),
        definition(
            kind="exclusion",
            table="public.users",
            filters=[{"column": "public.users.deleted_at", "operator": "is_not_null"}],
        ),
        definition(kind="canonical_user_id", column="public.users.user_id"),
    ],
    ids=["term-table", "term-column", "status", "window", "window-column", "exclusion", "canon"],
)
def test_every_entry_kind_parses(payload: dict[str, Any]) -> None:
    parsed = BusinessContextIn.model_validate(payload)

    assert parsed.definition.kind == payload["definition"]["kind"]


@pytest.mark.parametrize("key", ["", "Purchase", "1st", "a-b", "a b", "x" * 65, "purchase."])
def test_key_must_be_a_lowercase_slug(key: str) -> None:
    with pytest.raises(ValidationError):
        BusinessContextIn.model_validate(entry(key=key))


def test_synonyms_are_normalized_unique_and_sorted() -> None:
    parsed = BusinessContextIn.model_validate(
        entry(synonyms=["  Bought ", "bought", "Made   a  PURCHASE", "paid order"])
    )

    assert parsed.synonyms == ("bought", "made a purchase", "paid order")


@pytest.mark.parametrize("synonyms", [[""], ["   "], ["x" * 101]])
def test_blank_or_overlong_synonyms_refused(synonyms: list[str]) -> None:
    with pytest.raises(ValidationError):
        BusinessContextIn.model_validate(entry(synonyms=synonyms))


def test_description_is_free_text_with_a_limit() -> None:
    assert BusinessContextIn.model_validate(entry(description="")).description == ""
    with pytest.raises(ValidationError):
        BusinessContextIn.model_validate(entry(description="x" * 4001))


@pytest.mark.parametrize(
    "bad_filter",
    [
        {"column": "public.orders.status", "operator": "in", "value": []},
        {"column": "public.orders.status", "operator": "in", "value": "paid"},
        {"column": "public.orders.status", "operator": "not_in"},
        {"column": "public.orders.status", "operator": "=", "value": None},
        {"column": "public.orders.status", "operator": "=", "value": ["paid"]},
        {"column": "public.orders.status", "operator": "is_null", "value": "paid"},
        {"column": "public.orders.status", "operator": "LIKE", "value": "p%"},
        {"column": "orders.status", "operator": "=", "value": "paid"},
        {"column": "public.orders.status", "operator": "=", "value": "paid", "sql": "1=1"},
    ],
    ids=[
        "in-empty",
        "in-scalar",
        "not-in-missing",
        "eq-null",
        "eq-list",
        "is-null-with-value",
        "unknown-operator",
        "unqualified-column",
        "extra-field",
    ],
)
def test_malformed_filters_refused(bad_filter: dict[str, Any]) -> None:
    payload = definition(kind="metric", table="public.orders", filters=[bad_filter])

    with pytest.raises(ValidationError):
        BusinessContextIn.model_validate(payload)


def test_metric_columns_must_belong_to_the_metric_table() -> None:
    payload = definition(
        kind="metric",
        table="public.orders",
        filters=[{"column": "public.carts.status", "operator": "=", "value": "abandoned"}],
    )
    with pytest.raises(ValidationError, match="public.orders"):
        BusinessContextIn.model_validate(payload)

    with pytest.raises(ValidationError):
        BusinessContextIn.model_validate(
            definition(kind="metric", table="public.orders", value_column="public.users.user_id")
        )


def test_exclusion_needs_filters_on_its_table() -> None:
    with pytest.raises(ValidationError):
        BusinessContextIn.model_validate(
            definition(kind="exclusion", table="public.users", filters=[])
        )
    with pytest.raises(ValidationError):
        BusinessContextIn.model_validate(
            definition(
                kind="exclusion",
                table="public.users",
                filters=[{"column": "public.orders.status", "operator": "is_null"}],
            )
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "term"},
        {"kind": "term", "table": "public.users", "column": "public.users.user_id"},
        {"kind": "time_window", "last_days": 0},
        {"kind": "time_window", "last_days": 3651},
        {"kind": "status_semantics", "column": "public.orders.status", "meanings": {}},
        {"kind": "status_semantics", "column": "public.orders.status", "meanings": {"paid": ""}},
        {"kind": "canonical_user_id", "column": "public.users"},
        {"kind": "metric"},
        {"kind": "sql", "query": "SELECT 1"},
        {"kind": "time_window", "last_days": 7, "unexpected": True},
    ],
    ids=[
        "term-nothing",
        "term-both",
        "window-zero",
        "window-too-long",
        "status-empty",
        "status-blank-meaning",
        "canon-not-a-column",
        "metric-no-table",
        "unknown-kind",
        "extra-field",
    ],
)
def test_invalid_definitions_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BusinessContextIn.model_validate(definition(**fields))


def test_definition_references_lists_tables_and_columns() -> None:
    parsed = BusinessContextIn.model_validate(PURCHASE)

    tables, columns = definition_references(parsed.definition)

    assert tables == frozenset({"public.orders"})
    assert columns == frozenset(
        {"public.orders.status", "public.orders.grand_total", "public.orders.ordered_at"}
    )


def test_references_of_column_kinds_include_their_table() -> None:
    parsed = BusinessContextIn.model_validate(
        definition(kind="canonical_user_id", column="public.users.user_id")
    )

    assert definition_references(parsed.definition) == (
        frozenset({"public.users"}),
        frozenset({"public.users.user_id"}),
    )


def test_time_window_without_column_references_nothing() -> None:
    parsed = BusinessContextIn.model_validate(definition(kind="time_window", last_days=30))

    assert definition_references(parsed.definition) == (frozenset(), frozenset())


def test_definition_dump_is_canonical_json() -> None:
    parsed = BusinessContextIn.model_validate(PURCHASE)

    dumped = parsed.definition.model_dump(mode="json", exclude_none=True)

    assert dumped == {**PURCHASE["definition"]}
