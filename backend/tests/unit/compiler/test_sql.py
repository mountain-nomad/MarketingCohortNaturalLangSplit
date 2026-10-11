"""Deterministic SQL compilation (FR-6, AC-10, AC-2 guest carts, injection safety)."""

import random
from datetime import UTC, datetime
from typing import Any

import pytest

from cohortsplit.compiler.resolve import ResolvedCohort, ValueLookup, resolve
from cohortsplit.compiler.sql import (
    AS_OF_PARAM,
    CompiledQuery,
    UnsupportedDialectError,
    compile_cohort,
    compile_null_counts,
    compile_sample,
    compile_value_lookup,
    quote_ident,
    sql_hash,
)
from cohortsplit.compiler.validator import validate_select
from cohortsplit.semantic.snapshot import assemble_snapshot
from tests.unit.compiler.fixtures import (
    CARTS_HOP,
    DEFAULT_ENTRIES,
    INACTIVE,
    OPEN_POLICY,
    ORDER_TO_CATEGORY,
    ORDERS_HOP,
    attr,
    docs_for,
    flt,
    shop_snapshot,
    spec,
)

AS_OF = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
SNAPSHOT = shop_snapshot()
TABLES = frozenset(t.qualified_name for t in SNAPSHOT.tables)

ABANDONED_CART: dict[str, Any] = {
    "type": "related",
    "table": "public.carts",
    "path": [CARTS_HOP],
    "filters": [flt("public.carts.status", "=", "abandoned")],
}
TWO_DELIVERED: dict[str, Any] = {
    "type": "related",
    "table": "public.orders",
    "path": [ORDERS_HOP],
    "filters": [flt("public.orders.status", "=", "delivered")],
    "time_window": {"kind": "relative", "column": "public.orders.ordered_at", "last_days": 90},
    "aggregate": {"function": "count", "operator": ">=", "value": 2},
}
COMPLEX: dict[str, Any] = {
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
                    "time_window": {"kind": "term", "term": "recently"},
                    "aggregate": {"function": "sum", "operator": ">", "value": 500},
                },
            ],
        },
        {"type": "not", "condition": ABANDONED_CART},
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


def resolved(where: dict[str, Any] | None, snapshot: Any = SNAPSHOT) -> ResolvedCohort:
    result = resolve(spec(where), snapshot)
    assert isinstance(result, ResolvedCohort), result
    return result


def compiled(where: dict[str, Any] | None, snapshot: Any = SNAPSHOT) -> CompiledQuery:
    return compile_cohort(resolved(where, snapshot), as_of=AS_OF)


def test_related_exists_correlates_on_user_key() -> None:
    query = compiled(ABANDONED_CART)

    assert query.sql == (
        'SELECT DISTINCT u."user_id" AS "canonical_user_id" FROM "public"."users" AS u '
        'WHERE EXISTS (SELECT 1 FROM "public"."carts" AS t1 '
        'WHERE t1."user_id" = u."user_id" AND t1."status" = %(p1)s) ORDER BY 1'
    )
    assert query.params == {"p1": "abandoned"}


def test_guest_rows_cannot_match_because_membership_is_an_equality_on_the_user_key() -> None:
    # AC-2: carts with user_id IS NULL never satisfy t1.user_id = u.user_id, and every row
    # comes from the canonical user table, so no NULL id can be produced.
    query = compiled(ABANDONED_CART)

    assert 'FROM "public"."users" AS u' in query.sql
    assert 't1."user_id" = u."user_id"' in query.sql
    assert "IS NULL" not in query.sql
    assert " OR " not in query.sql


def test_attribute_filters_are_two_valued() -> None:
    query = compiled(attr("public.users.is_active", "=", True))

    assert query.sql == (
        'SELECT DISTINCT u."user_id" AS "canonical_user_id" FROM "public"."users" AS u '
        'WHERE COALESCE(u."is_active" = %(p1)s, FALSE) ORDER BY 1'
    )
    assert query.params == {"p1": True}


def test_count_threshold_counts_distinct_target_rows_in_a_relative_window() -> None:
    query = compiled(TWO_DELIVERED)

    assert query.sql == (
        'SELECT DISTINCT u."user_id" AS "canonical_user_id" FROM "public"."users" AS u '
        'WHERE (SELECT COUNT(*) FROM (SELECT DISTINCT t1."order_id" FROM "public"."orders" AS t1 '
        'WHERE t1."user_id" = u."user_id" AND t1."status" = %(p1)s '
        "AND t1.\"ordered_at\" >= CAST(%(as_of)s AS TIMESTAMPTZ) - %(p2)s * INTERVAL '1 day' "
        'AND t1."ordered_at" <= CAST(%(as_of)s AS TIMESTAMPTZ)) AS agg) >= %(p3)s ORDER BY 1'
    )
    assert query.params == {
        "p1": "delivered",
        "p2": 90,
        "p3": 2,
        AS_OF_PARAM: "2026-03-02T09:00:00+00:00",
    }


def test_sum_threshold_sums_distinct_target_rows_and_treats_none_as_zero() -> None:
    query = compiled(
        {
            "type": "metric",
            "term": "purchase",
            "aggregate": {"function": "sum", "operator": ">", "value": 500},
        }
    )

    assert (
        'COALESCE((SELECT SUM(agg."v") FROM (SELECT DISTINCT t1."order_id", '
        't1."grand_total" AS "v" FROM "public"."orders" AS t1 '
        'WHERE t1."user_id" = u."user_id" AND t1."status" IN (%(p1)s, %(p2)s, %(p3)s)) AS agg), 0) '
        "> %(p4)s"
    ) in query.sql
    assert query.params == {"p1": "paid", "p2": "shipped", "p3": "delivered", "p4": 500}


def test_multi_hop_paths_join_in_order() -> None:
    query = compiled(COMPLEX["conditions"][1]["conditions"][0])

    assert (
        'FROM "public"."orders" AS t1 '
        'JOIN "public"."order_items" AS t2 ON t2."order_id" = t1."order_id" '
        'JOIN "public"."product_variants" AS t3 ON t3."variant_id" = t2."variant_id" '
        'JOIN "public"."products" AS t4 ON t4."product_id" = t3."product_id" '
        'JOIN "public"."categories" AS t5 ON t5."category_id" = t4."category_id" '
        'WHERE t1."user_id" = u."user_id"'
    ) in query.sql


def test_negation_is_not_exists() -> None:
    query = compiled({"type": "not", "condition": ABANDONED_CART})
    assert 'WHERE NOT (EXISTS (SELECT 1 FROM "public"."carts" AS t1' in query.sql


def test_absolute_windows_bind_utc_bounds() -> None:
    query = compiled(COMPLEX["conditions"][3])

    assert (
        'COALESCE(u."created_at" >= CAST(%(p1)s AS TIMESTAMPTZ) '
        'AND u."created_at" < CAST(%(p2)s AS TIMESTAMPTZ), FALSE)'
    ) in query.sql
    assert query.params == {"p1": "2024-01-01T00:00:00+00:00", "p2": "2025-01-01T00:00:00+00:00"}


def test_exclusions_are_applied_as_not_exists() -> None:
    snapshot = shop_snapshot(entries=[*DEFAULT_ENTRIES, INACTIVE])
    query = compiled(None, snapshot)

    assert query.sql == (
        'SELECT DISTINCT u."user_id" AS "canonical_user_id" FROM "public"."users" AS u '
        'WHERE NOT (EXISTS (SELECT 1 FROM "public"."users" AS t0 WHERE t0."user_id" = u."user_id" '
        'AND t0."is_active" = %(p1)s)) ORDER BY 1'
    )
    assert query.params == {"p1": False}


def test_every_user_without_conditions() -> None:
    assert compiled(None).sql == (
        'SELECT DISTINCT u."user_id" AS "canonical_user_id" FROM "public"."users" AS u ORDER BY 1'
    )


def test_same_spec_and_version_compile_byte_identical() -> None:
    first = compiled(COMPLEX)
    second = compiled(COMPLEX)

    assert first.sql.encode() == second.sql.encode()
    assert first.params == second.params
    assert sql_hash(first) == sql_hash(second)


def test_compilation_independent_of_snapshot_order() -> None:
    docs = docs_for()
    shuffled = list(docs)
    random.Random(7).shuffle(shuffled)  # noqa: S311 (test data order)
    entries = list(DEFAULT_ENTRIES)
    random.Random(3).shuffle(entries)  # noqa: S311 (test data order)
    other = assemble_snapshot(
        entries=entries, use_cases=[], generated_docs=shuffled, policy=OPEN_POLICY
    )

    assert compiled(COMPLEX, other) == compiled(COMPLEX)


def test_sql_hash_ignores_execution_time_but_not_definitions() -> None:
    first = compile_cohort(resolved(TWO_DELIVERED), as_of=AS_OF)
    later = compile_cohort(resolved(TWO_DELIVERED), as_of=datetime(2027, 1, 1, tzinfo=UTC))
    other = compiled(ABANDONED_CART)

    assert first.sql == later.sql
    assert sql_hash(first) == sql_hash(later)
    assert sql_hash(first) != sql_hash(other)


@pytest.mark.parametrize(
    "value",
    [
        "x'); DROP TABLE users; --",
        'abandoned" OR 1=1 --',
        "%(as_of)s",
        "$$; DELETE FROM users; $$",
        "/* comment */",
    ],
)
def test_literals_are_bound_parameters_never_sql(value: str) -> None:
    query = compile_value_lookup(ValueLookup("public.products.name", (value,)))
    cohort = compile_cohort(
        resolved(
            {
                "type": "related",
                "table": "public.orders",
                "path": ORDER_TO_CATEGORY[:4],
                "filters": [flt("public.products.name", "=", value)],
            }
        ),
        as_of=AS_OF,
    )

    for compiled_query in (query, cohort):
        assert value not in compiled_query.sql
        assert value in compiled_query.params.values()
        validate_select(compiled_query.sql, allowed_tables=TABLES)


def test_identifiers_are_always_quoted() -> None:
    assert quote_ident("users") == '"users"'
    assert quote_ident('we"ird') == '"we""ird"'
    with pytest.raises(ValueError):
        quote_ident("bad\x00name")


def test_every_compiled_query_passes_the_validator() -> None:
    result = resolved(COMPLEX)
    queries = [
        compile_cohort(result, as_of=AS_OF),
        compile_sample(result, columns=["public.users.phone"], limit=20, as_of=AS_OF),
        compile_null_counts(result, columns=["public.users.phone"], as_of=AS_OF),
        compile_value_lookup(ValueLookup("public.products.product_id", (1, 2))),
    ]
    for query in queries:
        validate_select(query.sql, allowed_tables=TABLES)


def test_sample_selects_only_the_id_and_requested_user_columns() -> None:
    query = compile_sample(
        resolved(ABANDONED_CART), columns=["public.users.phone"], limit=20, as_of=AS_OF
    )

    assert query.sql.startswith(
        'SELECT DISTINCT u."user_id" AS "canonical_user_id", u."phone" AS "public.users.phone" '
        'FROM "public"."users" AS u WHERE EXISTS'
    )
    assert query.sql.endswith("ORDER BY 1 LIMIT 20")


def test_sample_refuses_columns_outside_the_user_table() -> None:
    with pytest.raises(ValueError, match="public.orders.status"):
        compile_sample(
            resolved(ABANDONED_CART), columns=["public.orders.status"], limit=5, as_of=AS_OF
        )


def test_null_counts_per_selected_column() -> None:
    query = compile_null_counts(
        resolved(ABANDONED_CART),
        columns=["public.users.phone", "public.users.first_name"],
        as_of=AS_OF,
    )

    assert query.sql.startswith(
        'SELECT COUNT(*) AS "total", COUNT(*) - COUNT(u."phone") AS "null_1", '
        'COUNT(*) - COUNT(u."first_name") AS "null_2" FROM "public"."users" AS u WHERE EXISTS'
    )


def test_value_lookup_query() -> None:
    query = compile_value_lookup(ValueLookup("public.products.name", ("Chai", "Chang")))

    assert query.sql == (
        'SELECT DISTINCT t."name" FROM "public"."products" AS t '
        'WHERE t."name" IN (%(p1)s, %(p2)s)'
    )
    assert query.params == {"p1": "Chai", "p2": "Chang"}


def test_only_postgresql_is_supported() -> None:
    with pytest.raises(UnsupportedDialectError):
        compile_cohort(resolved(None), as_of=AS_OF, dialect="snowflake")


def test_not_equal_and_not_in_treat_null_like_not_equals() -> None:
    """Review finding 5 / ruling C10: 'first_name ≠ Zed' and 'NOT first_name = Zed' select
    the same users, including those with an unknown (NULL) first name."""
    not_equal = compiled(attr("public.users.first_name", "!=", "Zed"))
    negated = compiled({"type": "not", "condition": attr("public.users.first_name", "=", "Zed")})
    not_in = compiled(attr("public.users.first_name", "not_in", ["Zed", "Ann"]))

    assert 'NOT COALESCE(u."first_name" = %(p1)s, FALSE)' in not_equal.sql
    assert 'NOT (COALESCE(u."first_name" = %(p1)s, FALSE))' in negated.sql
    assert 'NOT COALESCE(u."first_name" IN (%(p1)s, %(p2)s), FALSE)' in not_in.sql
    assert "<>" not in not_equal.sql
