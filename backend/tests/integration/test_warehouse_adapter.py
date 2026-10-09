"""PostgreSQL adapter introspection against the demo warehouse (FR-1, FR-2)."""

import pytest
from pydantic import SecretStr

from cohortsplit.warehouse import PostgresWarehouseAdapter, ReadOnlyExecutor, TableRef
from tests.integration.conftest import ScratchWarehouse

pytestmark = pytest.mark.integration


@pytest.fixture
def adapter(warehouse_ro_dsn: str) -> PostgresWarehouseAdapter:
    return PostgresWarehouseAdapter(ReadOnlyExecutor(SecretStr(warehouse_ro_dsn)))


def _table(adapter: PostgresWarehouseAdapter, name: str) -> TableRef:
    return next(t for t in adapter.list_tables("public") if t.name == name)


def test_test_connection(adapter: PostgresWarehouseAdapter) -> None:
    adapter.test_connection()


def test_list_schemas_excludes_system_schemas(adapter: PostgresWarehouseAdapter) -> None:
    schemas = adapter.list_schemas()

    assert "public" in schemas
    assert not {"pg_catalog", "information_schema", "pg_toast"} & set(schemas)
    assert schemas == sorted(schemas)


def test_list_tables_returns_demo_tables(adapter: PostgresWarehouseAdapter) -> None:
    tables = adapter.list_tables("public")

    names = [t.name for t in tables]
    assert {"users", "orders", "carts", "order_items", "products"} <= set(names)
    assert names == sorted(names)
    assert all(t.schema == "public" and t.kind == "table" for t in tables)


def test_describe_orders(adapter: PostgresWarehouseAdapter) -> None:
    meta = adapter.describe_table(_table(adapter, "orders"))

    columns = {c.name: c for c in meta.columns}
    assert meta.primary_key == ("order_id",)
    assert [c.name for c in meta.columns][:2] == ["order_id", "user_id"]
    assert columns["status"].type_category == "string"
    assert columns["status"].allowed_values == (
        "pending",
        "paid",
        "processing",
        "shipped",
        "delivered",
        "cancelled",
    )
    assert columns["grand_total"].type_category == "numeric"
    assert columns["ordered_at"].type_category == "datetime"
    assert columns["ordered_at"].data_type == "timestamp with time zone"
    fk = next(f for f in meta.foreign_keys if f.referred_table == "users")
    assert fk.columns == ("user_id",)
    assert fk.referred_columns == ("user_id",)
    assert fk.referred_schema == "public"
    assert meta.estimated_row_count is not None
    assert meta.estimated_row_count > 0


def test_describe_composite_key_and_citext(adapter: PostgresWarehouseAdapter) -> None:
    items = adapter.describe_table(_table(adapter, "order_items"))
    users = adapter.describe_table(_table(adapter, "users"))

    assert items.primary_key == ("order_id", "variant_id")
    assert {f.referred_table for f in items.foreign_keys} == {"orders", "product_variants"}
    email = next(c for c in users.columns if c.name == "email")
    assert email.type_category == "string"


def test_row_count_for_never_analyzed_table(adapter: PostgresWarehouseAdapter) -> None:
    meta = adapter.describe_table(_table(adapter, "carts"))

    assert meta.estimated_row_count == 10


def test_distinct_values_low_cardinality(adapter: PostgresWarehouseAdapter) -> None:
    values = adapter.get_distinct_values(_table(adapter, "orders"), "status", 50)

    assert values is not None
    assert values[0] == "delivered"  # most frequent first
    assert set(values) <= {"pending", "paid", "processing", "shipped", "delivered", "cancelled"}


def test_distinct_values_none_when_above_cap(adapter: PostgresWarehouseAdapter) -> None:
    assert adapter.get_distinct_values(_table(adapter, "users"), "first_name", 5) is None


def test_key_examples(adapter: PostgresWarehouseAdapter) -> None:
    assert adapter.get_key_examples(_table(adapter, "products"), "product_id", 3) == ["1", "2", "3"]


def test_partitioned_table_listed_once_and_counted_from_partitions(
    adapter: PostgresWarehouseAdapter, scratch: ScratchWarehouse
) -> None:
    scratch.admin(
        "CREATE TABLE {s}.events (id bigint, at date NOT NULL) PARTITION BY RANGE (at);"
        "CREATE TABLE {s}.events_2025 PARTITION OF {s}.events "
        "FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');"
        "CREATE TABLE {s}.events_2026 PARTITION OF {s}.events "
        "FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');"
        "INSERT INTO {s}.events SELECT i, DATE '2025-06-01' + (i % 400) "
        "FROM generate_series(1, 500) i;"
        "ANALYZE {s}.events;"
        "GRANT SELECT ON ALL TABLES IN SCHEMA {s} TO cohortsplit_ro;"
    )

    tables = {t.name: t for t in adapter.list_tables(scratch.schema)}

    assert "events" in tables
    assert "events_2025" not in tables
    assert tables["events"].kind == "partitioned_table"
    meta = adapter.describe_table(tables["events"])
    assert meta.estimated_row_count == 500
