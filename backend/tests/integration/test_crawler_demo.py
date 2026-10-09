"""Crawling the demo `ecommerce` warehouse as `cohortsplit_ro` (AC-22, AC-27)."""

import psycopg
import pytest
from pydantic import SecretStr

from cohortsplit.crawler.catalog import WarehouseCatalog
from cohortsplit.crawler.metadata import collect_catalog
from cohortsplit.crawler.sampling import SamplingPolicy
from cohortsplit.crawler.settings import DEFAULT_SAMPLE_DENYLIST
from cohortsplit.warehouse import PostgresWarehouseAdapter, ReadOnlyExecutor

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def demo_adapter(warehouse_ro_dsn: str) -> PostgresWarehouseAdapter:
    return PostgresWarehouseAdapter(ReadOnlyExecutor(SecretStr(warehouse_ro_dsn)))


@pytest.fixture(scope="module")
def demo_catalog(demo_adapter: PostgresWarehouseAdapter) -> WarehouseCatalog:
    policy = SamplingPolicy(enabled=True, denylist=DEFAULT_SAMPLE_DENYLIST)
    return collect_catalog(demo_adapter, policy, schemas=["public"])


def _reference_columns(dsn: str) -> dict[str, list[str]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
        ).fetchall()
    out: dict[str, list[str]] = {}
    for table, column in rows:
        out.setdefault(f"public.{table}", []).append(column)
    return out


def test_demo_crawl_lists_all_tables_and_columns(
    demo_catalog: WarehouseCatalog, warehouse_ro_dsn: str
) -> None:
    reference = _reference_columns(warehouse_ro_dsn)

    documented = {t.qualified_name: [c.name for c in t.columns] for t in demo_catalog.tables}

    assert len(reference) == 16
    assert documented == reference


def test_demo_crawl_pk_fk_relationships(demo_catalog: WarehouseCatalog) -> None:
    users = demo_catalog.table("public.users")
    items = demo_catalog.table("public.order_items")
    orders = demo_catalog.table("public.orders")
    assert users is not None
    assert items is not None
    assert orders is not None
    assert users.primary_key == ("user_id",)
    assert items.primary_key == ("order_id", "variant_id")
    assert {(f.columns, f.referred_table) for f in orders.foreign_keys} == {
        (("user_id",), "public.users")
    }
    assert {f.referred_table for f in items.foreign_keys} == {
        "public.orders",
        "public.product_variants",
    }
    total_fks = sum(len(t.foreign_keys) for t in demo_catalog.tables)
    assert total_fks == 18


def test_demo_crawl_row_counts(demo_catalog: WarehouseCatalog) -> None:
    counts = {p.table: p.estimated_row_count for p in demo_catalog.profiles}

    assert len(counts) == 16
    assert all(c is not None and c > 0 for c in counts.values()), counts
    assert counts["public.users"] == 91
    assert counts["public.orders"] == 830
    assert counts["public.carts"] == 10


def test_demo_crawl_samples_order_and_cart_status(demo_catalog: WarehouseCatalog) -> None:
    order_status = demo_catalog.sample_values("public.orders", "status")
    cart_status = demo_catalog.sample_values("public.carts", "status")

    assert order_status
    assert order_status[0] == "delivered"
    assert set(order_status) <= {
        "pending",
        "paid",
        "processing",
        "shipped",
        "delivered",
        "cancelled",
    }
    assert cart_status == ("active",)
    assert demo_catalog.sample_values("public.addresses", "country_code")


@pytest.mark.parametrize("column", ["email", "phone", "password_hash"])
def test_no_samples_for_denylisted_user_columns(
    demo_catalog: WarehouseCatalog, column: str
) -> None:
    profile = demo_catalog.profile("public.users")
    assert profile is not None
    entry = profile.column(column)
    assert entry is not None

    assert entry.sampled is False
    assert entry.values is None
    assert "denylist" in entry.reason
    assert profile.key_examples == ()


def test_no_pii_values_anywhere_in_catalog(
    demo_catalog: WarehouseCatalog, warehouse_ro_dsn: str
) -> None:
    with psycopg.connect(warehouse_ro_dsn) as conn:
        pii = conn.execute(
            "SELECT email::text, phone, password_hash FROM users ORDER BY user_id LIMIT 20"
        ).fetchall()
    dumped = demo_catalog.model_dump_json()

    for row in pii:
        for value in row:
            if value:
                assert str(value) not in dumped
