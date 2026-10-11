"""Readable interpretation shown before execution (FR-5)."""

from typing import Any

from cohortsplit.compiler.readable import readable
from cohortsplit.compiler.resolve import ResolvedCohort, resolve
from tests.unit.compiler.fixtures import (
    ADDRESSES_HOP,
    CARTS_HOP,
    DEFAULT_ENTRIES,
    INACTIVE,
    ORDER_TO_CATEGORY,
    ORDERS_HOP,
    attr,
    flt,
    shop_snapshot,
    spec,
)


def text(where: dict[str, Any] | None, entries: Any = DEFAULT_ENTRIES) -> str:
    result = resolve(spec(where), shop_snapshot(entries=entries))
    assert isinstance(result, ResolvedCohort), result
    return readable(result)


def test_ac1_count_threshold_reads_like_the_spec() -> None:
    assert (
        text(
            {
                "type": "related",
                "table": "public.orders",
                "path": [ORDERS_HOP],
                "filters": [flt("public.orders.status", "=", "delivered")],
                "time_window": {
                    "kind": "relative",
                    "column": "public.orders.ordered_at",
                    "last_days": 90,
                },
                "aggregate": {"function": "count", "operator": ">=", "value": 2},
            }
        )
        == "count(orders where status = delivered and ordered_at within 90 days) ≥ 2"
    )


def test_ac2_conjunction_with_negated_metric() -> None:
    assert (
        text(
            {
                "type": "all_of",
                "conditions": [
                    {
                        "type": "related",
                        "table": "public.addresses",
                        "path": [ADDRESSES_HOP],
                        "filters": [flt("public.addresses.country_code", "=", "KZ")],
                    },
                    {
                        "type": "related",
                        "table": "public.carts",
                        "path": [CARTS_HOP],
                        "filters": [flt("public.carts.status", "=", "abandoned")],
                    },
                    {"type": "not", "condition": {"type": "metric", "term": "paid_order"}},
                ],
            }
        )
        == "has addresses with country_code = KZ AND has carts with status = abandoned "
        "AND NOT has paid_order"
    )


def test_nested_groups_are_parenthesized_and_other_tables_qualified() -> None:
    assert (
        text(
            {
                "type": "all_of",
                "conditions": [
                    attr("public.users.is_active", "=", True),
                    {
                        "type": "any_of",
                        "conditions": [
                            {
                                "type": "metric",
                                "term": "purchase",
                                "path": ORDER_TO_CATEGORY,
                                "filters": [flt("public.categories.name", "=", "Beverages")],
                            },
                            {
                                "type": "metric",
                                "term": "purchase",
                                "aggregate": {"function": "sum", "operator": ">", "value": 500},
                            },
                        ],
                    },
                ],
            }
        )
        == "is_active = true AND (has purchase with categories.name = Beverages "
        "OR sum(grand_total of purchase) > 500)"
    )


def test_operators_lists_nulls_and_absolute_windows() -> None:
    assert (
        text(
            {
                "type": "all_of",
                "conditions": [
                    attr("public.users.first_name", "in", ["Ann", "Bob"]),
                    attr("public.users.phone", "is_not_null"),
                    attr("public.users.first_name", "!=", "Zed"),
                    {
                        "type": "attribute_time_window",
                        "window": {
                            "kind": "absolute",
                            "column": "public.users.created_at",
                            "start": "2024-01-01",
                            "end": "2025-01-01",
                        },
                    },
                ],
            }
        )
        == "first_name in (Ann, Bob) AND phone is not null AND first_name ≠ Zed "
        "AND created_at from 2024-01-01 to 2025-01-01"
    )


def test_term_window_shows_the_resolved_definition() -> None:
    assert (
        text(
            {
                "type": "metric",
                "term": "purchase",
                "time_window": {"kind": "term", "term": "recently"},
            }
        )
        == "has purchase with ordered_at within 30 days"
    )


def test_every_user_and_exclusions() -> None:
    assert text(None) == "all users"
    assert text(None, entries=[*DEFAULT_ENTRIES, INACTIVE]) == "all users AND NOT inactive_users"
    assert (
        text(attr("public.users.is_active", "=", True), entries=[*DEFAULT_ENTRIES, INACTIVE])
        == "is_active = true AND NOT inactive_users"
    )


def test_negated_group() -> None:
    assert (
        text(
            {
                "type": "not",
                "condition": {
                    "type": "any_of",
                    "conditions": [
                        attr("public.users.is_active", "=", False),
                        attr("public.users.phone", "is_null"),
                    ],
                },
            }
        )
        == "NOT (is_active = false OR phone is null)"
    )
