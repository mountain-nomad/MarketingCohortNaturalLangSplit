"""AC-1 .. AC-5 against the demo ``ecommerce`` warehouse (integration).

For each acceptance request the fake LLM returns the spec a correct interpreter would
produce from the seeded business context. The test asserts the readable interpretation
and that the compiled cohort's user-id set equals a hand-written reference query.

Demo-data substitutions (the AC text uses placeholders; see the plan):

* "Kazakhstan" -> Germany (``DE``): the demo has no ``KZ`` address.
* "Electronics" -> category ``Beverages``; "product X" -> ``Product HHYDP`` (AC-3, a
  Beverages product) and ``Product HMLNI`` (AC-5).
* Demo orders are from 2006-2008, so AC-1's "last 90 days" is evaluated at execution time
  2008-05-07 (the bound ``as_of``; relative windows are always relative to it).
* Demo users are created when the warehouse is seeded, and the demo has no abandoned carts:
  the literal AC-2 and "registered in 2024" requests are run and compared (empty == empty),
  and non-empty variants with real demo values are compared as well.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.auth.helpers import FakeClock
from tests.cohort.helpers import INTERPRET, PREVIEW, confirm
from tests.cohort_reference.world import World, build_world, reference
from tests.llm_fakes import interpretation

pytestmark = pytest.mark.integration

S = "public"
USERS = f"{S}.users"


def hop(a: str, b: str) -> dict[str, str]:
    return {"from_column": f"{S}.{a}", "to_column": f"{S}.{b}"}


TO_ORDERS = [hop("users.user_id", "orders.user_id")]
TO_PRODUCTS = [
    *TO_ORDERS,
    hop("orders.order_id", "order_items.order_id"),
    hop("order_items.variant_id", "product_variants.variant_id"),
    hop("product_variants.product_id", "products.product_id"),
]
TO_CATEGORIES = [*TO_PRODUCTS, hop("products.category_id", "categories.category_id")]
PAID = "('paid', 'shipped', 'delivered')"
BOUGHT_PRODUCT_SQL = f"""
    EXISTS (SELECT 1 FROM public.orders o
            JOIN public.order_items oi ON oi.order_id = o.order_id
            JOIN public.product_variants pv ON pv.variant_id = oi.variant_id
            JOIN public.products p ON p.product_id = pv.product_id
            WHERE o.user_id = u.user_id AND o.status IN {PAID} AND p.name = %(product)s)
"""


@pytest.fixture
def world(
    app: FastAPI, engine: Engine, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> World:
    return build_world(app, engine, db, clock, ro_dsn=warehouse_ro_dsn, schema=S)


def check(
    world: World,
    db: Session,
    clock: FakeClock,
    *,
    request: str,
    spec_where: dict[str, Any],
    readable: str,
    expected: set[object],
    non_empty: bool = True,
) -> dict[str, Any]:
    result = world.run(request, {"spec_version": "1", "entity": "user", "where": spec_where})
    assert result["interpretation"]["readable"] == readable
    preview = result["preview"]
    members = world.members(preview, db, clock)
    assert members == expected
    assert preview["total_count"] == len(expected)
    if non_empty:
        assert expected, "reference result must not be empty for a meaningful comparison"
    return preview


def test_ac1_two_delivered_orders_in_last_90_days(
    app: FastAPI, engine: Engine, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> None:
    clock.now = datetime(2008, 5, 7, tzinfo=UTC)  # demo orders end on 2008-05-06
    world = build_world(app, engine, db, clock, ro_dsn=warehouse_ro_dsn, schema=S)
    expected = reference(
        warehouse_ro_dsn,
        """
        SELECT o.user_id FROM public.orders o
        WHERE o.status = 'delivered'
          AND o.ordered_at >= %(as_of)s - INTERVAL '90 days' AND o.ordered_at <= %(as_of)s
        GROUP BY o.user_id HAVING count(*) >= 2
        """,
        {"as_of": clock.now},
    )

    preview = check(
        world,
        db,
        clock,
        request="Users who placed at least 2 delivered orders in the last 90 days",
        spec_where={
            "type": "related",
            "table": f"{S}.orders",
            "path": TO_ORDERS,
            "filters": [{"column": f"{S}.orders.status", "operator": "=", "value": "delivered"}],
            "time_window": {
                "kind": "relative",
                "column": f"{S}.orders.ordered_at",
                "last_days": 90,
            },
            "aggregate": {"function": "count", "operator": ">=", "value": 2},
        },
        readable="count(orders where status = delivered and ordered_at within 90 days) ≥ 2",
        expected=expected,
    )
    assert preview["as_of"] == clock.now.isoformat()


AC2_SQL = f"""
    SELECT u.user_id FROM public.users u
    WHERE EXISTS (SELECT 1 FROM public.addresses a
                  WHERE a.user_id = u.user_id AND a.country_code = %(country)s)
      AND EXISTS (SELECT 1 FROM public.carts c
                  WHERE c.user_id = u.user_id AND c.status = %(cart_status)s)
      AND NOT EXISTS (SELECT 1 FROM public.orders o
                      WHERE o.user_id = u.user_id AND o.status IN {PAID})
"""


def ac2_spec(cart_status: str, negated_metric: str) -> dict[str, Any]:
    return {
        "type": "all_of",
        "conditions": [
            {
                "type": "related",
                "table": f"{S}.addresses",
                "path": [hop("users.user_id", "addresses.user_id")],
                "filters": [
                    {"column": f"{S}.addresses.country_code", "operator": "=", "value": "DE"}
                ],
            },
            {
                "type": "related",
                "table": f"{S}.carts",
                "path": [hop("users.user_id", "carts.user_id")],
                "filters": [{"column": f"{S}.carts.status", "operator": "=", "value": cart_status}],
            },
            {"type": "not", "condition": {"type": "metric", "term": negated_metric}},
        ],
    }


def test_ac2_country_abandoned_cart_never_paid(
    world: World, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> None:
    expected = reference(warehouse_ro_dsn, AC2_SQL, {"country": "DE", "cart_status": "abandoned"})

    preview = check(
        world,
        db,
        clock,
        request="Users from Germany who abandoned a cart and never paid for an order",
        spec_where=ac2_spec("abandoned", "paid_order"),
        readable=(
            "has addresses with country_code = DE AND has carts with status = abandoned "
            "AND NOT has paid_order"
        ),
        expected=expected,
        non_empty=False,  # the demo has no abandoned carts
    )
    assert preview["empty"] is True
    assert preview["split_export_enabled"] is False


def test_ac2_variant_with_real_demo_carts(
    world: World, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> None:
    # Same shape on data the demo has: German users with an active cart and no
    # discounted order (the NOT across a relation is what AC-2 exercises).
    expected = reference(
        warehouse_ro_dsn,
        """
        SELECT u.user_id FROM public.users u
        WHERE EXISTS (SELECT 1 FROM public.addresses a
                      WHERE a.user_id = u.user_id AND a.country_code = 'DE')
          AND EXISTS (SELECT 1 FROM public.carts c
                      WHERE c.user_id = u.user_id AND c.status = 'active')
          AND NOT EXISTS (SELECT 1 FROM public.orders o
                          WHERE o.user_id = u.user_id AND o.discount_total > 0)
        """,
    )

    check(
        world,
        db,
        clock,
        request="Users from Germany with an active cart who never used a discount",
        spec_where=ac2_spec("active", "discounted_order"),
        readable=(
            "has addresses with country_code = DE AND has carts with status = active "
            "AND NOT has discounted_order"
        ),
        expected=expected,
    )


def test_ac3_category_but_not_product(
    world: World, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> None:
    expected = reference(
        warehouse_ro_dsn,
        f"""
        SELECT u.user_id FROM public.users u
        WHERE EXISTS (SELECT 1 FROM public.orders o
                      JOIN public.order_items oi ON oi.order_id = o.order_id
                      JOIN public.product_variants pv ON pv.variant_id = oi.variant_id
                      JOIN public.products p ON p.product_id = pv.product_id
                      JOIN public.categories c ON c.category_id = p.category_id
                      WHERE o.user_id = u.user_id AND o.status IN {PAID}
                        AND c.name = 'Beverages')
          AND NOT {BOUGHT_PRODUCT_SQL}
        """,
        {"product": "Product HHYDP"},
    )

    check(
        world,
        db,
        clock,
        request="Users who bought anything from category Beverages but not product Product HHYDP",
        spec_where={
            "type": "all_of",
            "conditions": [
                {
                    "type": "metric",
                    "term": "purchase",
                    "path": TO_CATEGORIES,
                    "filters": [
                        {"column": f"{S}.categories.name", "operator": "=", "value": "Beverages"}
                    ],
                },
                {
                    "type": "not",
                    "condition": {
                        "type": "metric",
                        "term": "purchase",
                        "path": TO_PRODUCTS,
                        "filters": [
                            {
                                "column": f"{S}.products.name",
                                "operator": "=",
                                "value": "Product HHYDP",
                            }
                        ],
                    },
                },
            ],
        },
        readable=(
            "has purchase with categories.name = Beverages "
            "AND NOT has purchase with products.name = Product HHYDP"
        ),
        expected=expected,
    )


AC4_SQL = f"""
    SELECT u.user_id FROM public.users u
    WHERE u.is_active
      AND u.created_at >= %(start)s AND u.created_at < %(end)s
      AND (SELECT COALESCE(SUM(o.grand_total), 0) FROM public.orders o
           WHERE o.user_id = u.user_id AND o.status IN {PAID}) > 500
"""


def ac4_spec(year: int) -> dict[str, Any]:
    return {
        "type": "all_of",
        "conditions": [
            {
                "type": "attribute",
                "filter": {"column": f"{USERS}.is_active", "operator": "=", "value": True},
            },
            {
                "type": "attribute_time_window",
                "window": {
                    "kind": "absolute",
                    "column": f"{USERS}.created_at",
                    "start": f"{year}-01-01",
                    "end": f"{year + 1}-01-01",
                },
            },
            {
                "type": "metric",
                "term": "spent",
                "aggregate": {"function": "sum", "operator": ">", "value": 500},
            },
        ],
    }


def ac4_readable(year: int) -> str:
    return (
        f"is_active = true AND created_at from {year}-01-01 to {year + 1}-01-01 "
        "AND sum(grand_total of spent) > 500"
    )


def _bounds(year: int) -> dict[str, Any]:
    return {
        "start": datetime(year, 1, 1, tzinfo=UTC),
        "end": datetime(year + 1, 1, 1, tzinfo=UTC),
    }


def test_ac4_active_registered_in_2024_spent_over_500(
    world: World, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> None:
    check(
        world,
        db,
        clock,
        request="Active users who registered in 2024 and spent more than $500 in total",
        spec_where=ac4_spec(2024),
        readable=ac4_readable(2024),
        expected=reference(warehouse_ro_dsn, AC4_SQL, _bounds(2024)),
        non_empty=False,  # demo users are created when the warehouse is seeded
    )


def test_ac4_active_registered_in_seed_year_spent_over_500(
    world: World, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> None:
    [seed_year] = reference(
        warehouse_ro_dsn, "SELECT DISTINCT extract(year FROM created_at)::int FROM public.users"
    )
    assert isinstance(seed_year, int)

    check(
        world,
        db,
        clock,
        request=f"Active users who registered in {seed_year} and spent more than $500 in total",
        spec_where=ac4_spec(seed_year),
        readable=ac4_readable(seed_year),
        expected=reference(warehouse_ro_dsn, AC4_SQL, _bounds(seed_year)),
    )


def test_ac5_product_or_discount(
    world: World, db: Session, clock: FakeClock, warehouse_ro_dsn: str
) -> None:
    expected = reference(
        warehouse_ro_dsn,
        f"""
        SELECT u.user_id FROM public.users u
        WHERE {BOUGHT_PRODUCT_SQL}
           OR EXISTS (SELECT 1 FROM public.orders o
                      WHERE o.user_id = u.user_id AND o.discount_total > 0)
        """,
        {"product": "Product HMLNI"},
    )

    check(
        world,
        db,
        clock,
        request="Users who bought product Product HMLNI or used a discount",
        spec_where={
            "type": "any_of",
            "conditions": [
                {
                    "type": "metric",
                    "term": "purchase",
                    "path": TO_PRODUCTS,
                    "filters": [
                        {"column": f"{S}.products.name", "operator": "=", "value": "Product HMLNI"}
                    ],
                },
                {"type": "metric", "term": "used a discount"},
            ],
        },
        readable="has purchase with products.name = Product HMLNI OR has discounted_order",
        expected=expected,
    )


def test_unknown_product_name_is_refused_with_the_name(world: World) -> None:
    # Edge case: a product name that does not exist -> explicit unknown-value error.
    spec = {
        "spec_version": "1",
        "where": {
            "type": "metric",
            "term": "purchase",
            "path": TO_PRODUCTS,
            "filters": [
                {"column": f"{S}.products.name", "operator": "=", "value": "Electronics Gadget"}
            ],
        },
    }
    world.llm.replies[:] = [interpretation(spec)]
    interpreted = world.client.post(
        INTERPRET, json={"nl_request": "Users who bought Electronics Gadget"}
    ).json()
    assert interpreted["kind"] == "interpretation", interpreted

    response = world.client.post(PREVIEW, json=confirm(interpreted))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_value"
    assert "Electronics Gadget" in response.json()["error"]["message"]


def test_kazakhstan_is_not_a_demo_country_so_the_system_asks(world: World) -> None:
    world.llm.replies[:] = [
        interpretation(
            {
                "spec_version": "1",
                "where": {
                    "type": "related",
                    "table": f"{S}.addresses",
                    "filters": [
                        {"column": f"{S}.addresses.country_code", "operator": "=", "value": "KZ"}
                    ],
                },
            }
        )
    ]

    body = world.client.post(INTERPRET, json={"nl_request": "Users from Kazakhstan"}).json()

    assert body["kind"] == "clarification"
    assert body["unknown_values"][0]["column"] == "public.addresses.country_code"
    assert body["unknown_values"][0]["value"] == "KZ"
