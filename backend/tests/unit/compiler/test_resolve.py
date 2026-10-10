"""Deterministic semantic resolution of a spec against the snapshot (FR-4, BR-2, AC-6/AC-7).

The resolver never invents tables, columns, joins or definitions: every reference is
checked against the snapshot. Undefined business terms and unknown enumerable values
become a clarification naming them; invented schema references are invalid.
"""

import dataclasses

import pytest

from cohortsplit.compiler.resolve import (
    CanonicalUserIdMissingError,
    Invalid,
    ResolvedCohort,
    Unresolved,
    ValueLookup,
    resolve,
)
from tests.unit.compiler.fixtures import (
    ADDRESSES_HOP,
    CARTS_HOP,
    DEFAULT_ENTRIES,
    INACTIVE,
    ORDER_TO_CATEGORY,
    ORDERS_HOP,
    PURCHASE,
    attr,
    flt,
    hop,
    shop_snapshot,
    spec,
)

SNAPSHOT = shop_snapshot()


def resolved(where: dict[str, object] | None) -> ResolvedCohort:
    result = resolve(spec(where), SNAPSHOT)
    assert isinstance(result, ResolvedCohort), result
    return result


def invalid(where: dict[str, object]) -> Invalid:
    result = resolve(spec(where), SNAPSHOT)
    assert isinstance(result, Invalid), result
    return result


def unresolved(where: dict[str, object]) -> Unresolved:
    result = resolve(spec(where), SNAPSHOT)
    assert isinstance(result, Unresolved), result
    return result


def mentions(result: Invalid, text: str) -> bool:
    return any(text in problem for problem in result.problems)


def test_canonical_user_id_is_required() -> None:
    snapshot = shop_snapshot(entries=[e for e in DEFAULT_ENTRIES if e.key != "customer_id"])

    with pytest.raises(CanonicalUserIdMissingError, match="Business context"):
        resolve(spec(attr("public.users.is_active", "=", True)), snapshot)


def test_every_user_when_no_conditions() -> None:
    result = resolved(None)

    assert (result.user_table, result.user_key) == ("public.users", "user_id")
    assert result.where is None


def test_attribute_on_the_user_table() -> None:
    assert resolved(attr("public.users.is_active", "=", True)).where is not None


def test_attribute_on_another_table_is_invalid() -> None:
    result = invalid(attr("public.orders.status", "=", "paid"))
    assert mentions(result, "public.orders.status")


@pytest.mark.parametrize(
    ("where", "named"),
    [
        (attr("public.users.nope", "=", 1), "public.users.nope"),
        ({"type": "related", "table": "public.refunds"}, "public.refunds"),
        (
            {
                "type": "related",
                "table": "public.orders",
                "filters": [flt("public.orders.state", "=", "paid")],
            },
            "public.orders.state",
        ),
    ],
    ids=["unknown-column", "unknown-table", "unknown-filter-column"],
)
def test_invented_schema_references_are_invalid_and_named(
    where: dict[str, object], named: str
) -> None:
    assert mentions(invalid(where), named)


def test_direct_relationship_resolved_without_explicit_path() -> None:
    result = resolved(
        {
            "type": "related",
            "table": "public.carts",
            "filters": [flt("public.carts.status", "=", "abandoned")],
        }
    )
    assert result.where is not None


def test_ambiguous_relationship_requires_an_explicit_path() -> None:
    # users -> categories: through orders or through carts (same length).
    result = invalid(
        {
            "type": "related",
            "table": "public.categories",
            "filters": [flt("public.categories.name", "=", "Beverages")],
        }
    )
    assert mentions(result, "ambiguous")


def test_explicit_path_resolves_multi_hop_filters() -> None:
    result = resolved(
        {
            "type": "related",
            "table": "public.orders",
            "path": ORDER_TO_CATEGORY,
            "filters": [flt("public.categories.name", "=", "Beverages")],
        }
    )
    assert result.where is not None


def test_path_hop_without_a_foreign_key_is_invalid() -> None:
    result = invalid(
        {
            "type": "related",
            "table": "public.orders",
            "path": [hop("public.users.user_id", "public.orders.order_id")],
        }
    )
    assert mentions(result, "public.users.user_id -> public.orders.order_id")


def test_path_must_start_at_the_user_table() -> None:
    result = invalid(
        {
            "type": "related",
            "table": "public.order_items",
            "path": [hop("public.orders.order_id", "public.order_items.order_id")],
        }
    )
    assert mentions(result, "public.users")


def test_filter_column_must_be_on_the_path() -> None:
    result = invalid(
        {
            "type": "related",
            "table": "public.orders",
            "path": [ORDERS_HOP],
            "filters": [flt("public.carts.status", "=", "abandoned")],
        }
    )
    assert mentions(result, "public.carts.status")


def test_metric_term_resolves_definition_and_synonyms() -> None:
    for term in ("purchase", "Bought", "  PURCHASED "):
        assert resolved({"type": "metric", "term": term}).where is not None


def test_undefined_metric_term_asks_and_names_it() -> None:
    result = unresolved({"type": "metric", "term": "best customer"})

    assert result.unknown_terms == ("best customer",)
    assert "best customer" in result.question
    assert "Business context" in result.question


def test_term_of_the_wrong_kind_is_not_a_metric() -> None:
    result = unresolved({"type": "metric", "term": "country"})
    assert result.unknown_terms == ("country",)
    assert "not a metric" in result.question


def test_purchased_recently_asks_for_both_terms_when_undefined() -> None:
    snapshot = shop_snapshot(
        entries=[e for e in DEFAULT_ENTRIES if e.key not in {"purchase", "recently"}]
    )

    result = resolve(
        spec(
            {
                "type": "metric",
                "term": "purchase",
                "time_window": {"kind": "term", "term": "recently"},
            }
        ),
        snapshot,
    )

    assert isinstance(result, Unresolved)
    assert result.unknown_terms == ("purchase", "recently")


def test_defined_term_window_resolves_with_the_metric_time_column() -> None:
    result = resolved(
        {"type": "metric", "term": "purchase", "time_window": {"kind": "term", "term": "recently"}}
    )
    assert result.where is not None


def test_window_without_a_column_on_a_plain_relation_is_invalid() -> None:
    result = invalid(
        {
            "type": "related",
            "table": "public.carts",
            "time_window": {"kind": "relative", "last_days": 7},
        }
    )
    assert mentions(result, "date column")


def test_stale_business_context_is_treated_as_undefined() -> None:
    stale = dataclasses.replace(
        PURCHASE,
        definition={**PURCHASE.definition, "time_column": "public.orders.gone_at"},
    )
    snapshot = shop_snapshot(entries=[e for e in DEFAULT_ENTRIES if e.key != "purchase"] + [stale])

    result = resolve(spec({"type": "metric", "term": "purchase"}), snapshot)

    assert isinstance(result, Unresolved)
    assert result.unknown_terms == ("purchase",)


def test_unknown_enumerable_value_asks_with_suggestions() -> None:
    result = unresolved(
        {
            "type": "related",
            "table": "public.carts",
            "path": [CARTS_HOP],
            "filters": [flt("public.carts.status", "=", "abandonned")],
        }
    )

    [unknown] = result.unknown_values
    assert (unknown.column, unknown.value) == ("public.carts.status", "abandonned")
    assert "abandoned" in unknown.suggestions
    assert "abandonned" in result.question


def test_unknown_sampled_value_lists_known_values() -> None:
    # Demo-like case: no "XX" among the sampled country codes.
    result = unresolved(
        {
            "type": "related",
            "table": "public.addresses",
            "path": [ADDRESSES_HOP],
            "filters": [flt("public.addresses.country_code", "=", "XX")],
        }
    )
    [unknown] = result.unknown_values
    assert set(unknown.known) == {"KZ", "US", "DE"}


def test_values_without_a_known_domain_are_looked_up_at_preview() -> None:
    result = resolved(
        {
            "type": "related",
            "table": "public.orders",
            "path": [
                *ORDER_TO_CATEGORY[:4],
            ],
            "filters": [flt("public.products.name", "in", ["Chai", "Chang"])],
        }
    )
    assert result.lookups == (ValueLookup("public.products.name", ("Chai", "Chang")),)


def test_numeric_literals_are_coerced_or_refused() -> None:
    ok = resolved(
        {
            "type": "related",
            "table": "public.orders",
            "path": ORDER_TO_CATEGORY[:4],
            "filters": [flt("public.products.product_id", "=", "12")],
        }
    )
    assert ok.lookups == (ValueLookup("public.products.product_id", (12,)),)

    bad = invalid(
        {
            "type": "related",
            "table": "public.orders",
            "path": ORDER_TO_CATEGORY[:4],
            "filters": [flt("public.products.product_id", "=", "twelve")],
        }
    )
    assert mentions(bad, "public.products.product_id")


def test_boolean_literals_are_coerced_or_refused() -> None:
    assert resolved(attr("public.users.is_active", "=", "true")).where is not None
    assert mentions(invalid(attr("public.users.is_active", "=", "maybe")), "is_active")


def test_sum_needs_a_numeric_target_column() -> None:
    missing = invalid(
        {
            "type": "related",
            "table": "public.orders",
            "aggregate": {"function": "sum", "operator": ">", "value": 5},
        }
    )
    assert mentions(missing, "sum")
    elsewhere = invalid(
        {
            "type": "related",
            "table": "public.orders",
            "path": [ORDERS_HOP],
            "aggregate": {
                "function": "sum",
                "column": "public.users.user_id",
                "operator": ">",
                "value": 5,
            },
        }
    )
    assert mentions(elsewhere, "public.users.user_id")


def test_metric_sum_defaults_to_its_value_column() -> None:
    result = resolved(
        {
            "type": "metric",
            "term": "purchase",
            "aggregate": {"function": "sum", "operator": ">", "value": 500},
        }
    )
    assert result.where is not None


def test_metric_without_value_column_cannot_be_summed() -> None:
    result = invalid(
        {
            "type": "metric",
            "term": "paid_order",
            "aggregate": {"function": "sum", "operator": ">", "value": 500},
        }
    )
    assert mentions(result, "paid_order")


def test_exclusions_from_business_context_apply_to_every_cohort() -> None:
    snapshot = shop_snapshot(entries=[*DEFAULT_ENTRIES, INACTIVE])

    result = resolve(spec(None), snapshot)

    assert isinstance(result, ResolvedCohort)
    assert [e.key for e in result.exclusions] == ["inactive_users"]


def test_resolved_tables_cover_every_table_used() -> None:
    result = resolved(
        {
            "type": "all_of",
            "conditions": [
                {
                    "type": "metric",
                    "term": "purchase",
                    "path": ORDER_TO_CATEGORY,
                    "filters": [flt("public.categories.name", "=", "Beverages")],
                },
                {"type": "related", "table": "public.carts"},
            ],
        }
    )
    assert result.tables == {
        "public.users",
        "public.orders",
        "public.order_items",
        "public.product_variants",
        "public.products",
        "public.categories",
        "public.carts",
    }
