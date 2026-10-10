"""A semantic snapshot of the fake ecommerce warehouse plus business context, for compiler tests.

Built through the real crawler catalog and :func:`assemble_snapshot`, so the compiler sees
exactly the shapes it gets in production.
"""

from collections.abc import Iterable
from typing import Any

from cohortsplit.cohort_spec.v1 import CohortSpecV1
from cohortsplit.crawler.metadata import collect_catalog
from cohortsplit.crawler.sampling import DEFAULT_SAMPLE_DENYLIST, SamplingPolicy
from cohortsplit.crawler.store import table_doc_key
from cohortsplit.semantic.snapshot import SemanticContextSnapshot, assemble_snapshot
from cohortsplit.semantic.version import DocContent, EntryContent
from tests.fakes import FakeTable, shop_tables

OPEN_POLICY = SamplingPolicy(enabled=True, denylist=DEFAULT_SAMPLE_DENYLIST)


def entry(key: str, definition: dict[str, Any], synonyms: tuple[str, ...] = ()) -> EntryContent:
    return EntryContent(
        key=key,
        kind=str(definition["kind"]),
        synonyms=synonyms,
        description="",
        definition=definition,
    )


CANONICAL = entry("customer_id", {"kind": "canonical_user_id", "column": "public.users.user_id"})
PURCHASE = entry(
    "purchase",
    {
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
    synonyms=("bought", "purchased"),
)
PAID_ORDER = entry(
    "paid_order",
    {
        "kind": "metric",
        "table": "public.orders",
        "filters": [
            {
                "column": "public.orders.status",
                "operator": "in",
                "value": ["paid", "shipped", "delivered"],
            }
        ],
    },
)
RECENTLY = entry("recently", {"kind": "time_window", "last_days": 30})
COUNTRY = entry("country", {"kind": "term", "column": "public.addresses.country_code"})
INACTIVE = entry(
    "inactive_users",
    {
        "kind": "exclusion",
        "table": "public.users",
        "filters": [{"column": "public.users.is_active", "operator": "=", "value": False}],
    },
)

DEFAULT_ENTRIES = (CANONICAL, PURCHASE, PAID_ORDER, RECENTLY, COUNTRY)


def docs_for(tables: list[FakeTable] | None = None) -> list[DocContent]:
    from tests.fakes import FakeAdapter

    catalog = collect_catalog(FakeAdapter(tables or shop_tables()), OPEN_POLICY)
    docs = [
        DocContent(table_doc_key(t.qualified_name), "table_schema", t.model_dump(mode="json"))
        for t in catalog.tables
    ]
    docs += [
        DocContent(table_doc_key(p.table), "data_profile", p.model_dump(mode="json"))
        for p in catalog.profiles
    ]
    return docs


def shop_snapshot(
    entries: Iterable[EntryContent] = DEFAULT_ENTRIES,
    docs: list[DocContent] | None = None,
) -> SemanticContextSnapshot:
    return assemble_snapshot(
        entries=list(entries),
        use_cases=[],
        generated_docs=docs if docs is not None else docs_for(),
        policy=OPEN_POLICY,
    )


def spec(where: dict[str, Any] | None) -> CohortSpecV1:
    body: dict[str, Any] = {"spec_version": "1"}
    if where is not None:
        body["where"] = where
    return CohortSpecV1.model_validate(body)


def attr(column: str, operator: str, value: object = None) -> dict[str, Any]:
    flt: dict[str, Any] = {"column": column, "operator": operator}
    if value is not None:
        flt["value"] = value
    return {"type": "attribute", "filter": flt}


def flt(column: str, operator: str, value: object = None) -> dict[str, Any]:
    out: dict[str, Any] = {"column": column, "operator": operator}
    if value is not None:
        out["value"] = value
    return out


def hop(from_column: str, to_column: str) -> dict[str, str]:
    return {"from_column": from_column, "to_column": to_column}


ORDERS_HOP = hop("public.users.user_id", "public.orders.user_id")
CARTS_HOP = hop("public.users.user_id", "public.carts.user_id")
ADDRESSES_HOP = hop("public.users.user_id", "public.addresses.user_id")
ORDER_TO_CATEGORY = [
    ORDERS_HOP,
    hop("public.orders.order_id", "public.order_items.order_id"),
    hop("public.order_items.variant_id", "public.product_variants.variant_id"),
    hop("public.product_variants.product_id", "public.products.product_id"),
    hop("public.products.category_id", "public.categories.category_id"),
]
