"""Metadata collection with a fake adapter (FR-2, AC-22 shape, AC-27 enforcement)."""

import pytest

from cohortsplit.crawler.entities import detect_user_table
from cohortsplit.crawler.errors import CrawlScopeError
from cohortsplit.crawler.metadata import collect_catalog
from cohortsplit.crawler.sampling import SamplingPolicy
from cohortsplit.crawler.settings import DEFAULT_SAMPLE_DENYLIST
from cohortsplit.warehouse.errors import (
    QueryTimeoutError,
    WarehouseUnavailableError,
)
from cohortsplit.warehouse.models import TableRef
from tests.fakes import FakeAdapter, FakeTable, col, shop_tables


def _policy(enabled: bool = True, **kwargs: object) -> SamplingPolicy:
    return SamplingPolicy(enabled=enabled, denylist=DEFAULT_SAMPLE_DENYLIST, **kwargs)  # type: ignore[arg-type]


def test_catalog_documents_every_table_column_key_and_row_count() -> None:
    adapter = FakeAdapter(shop_tables())

    catalog = collect_catalog(adapter, _policy())

    assert [t.qualified_name for t in catalog.tables] == sorted(adapter.tables)
    orders = catalog.table("public.orders")
    assert orders is not None
    assert [c.name for c in orders.columns] == [
        "order_id",
        "user_id",
        "status",
        "grand_total",
        "ordered_at",
        "created_at",
    ]
    assert orders.primary_key == ("order_id",)
    assert orders.foreign_keys[0].referred_table == "public.users"
    assert orders.foreign_keys[0].columns == ("user_id",)
    status = orders.column("status")
    assert status is not None
    assert status.allowed_values is not None
    assert "delivered" in status.allowed_values
    profile = catalog.profile("public.orders")
    assert profile is not None
    assert profile.estimated_row_count == 830
    assert catalog.dialect == "postgresql"


def test_low_cardinality_samples_collected() -> None:
    catalog = collect_catalog(FakeAdapter(shop_tables()), _policy())

    assert catalog.sample_values("public.orders", "status") == (
        "delivered",
        "processing",
        "pending",
        "cancelled",
    )
    assert catalog.sample_values("public.carts", "status") == ("active",)
    assert catalog.sample_values("public.addresses", "country_code") == ("KZ", "US", "DE")


def test_denylisted_columns_are_never_queried() -> None:
    adapter = FakeAdapter(shop_tables())

    catalog = collect_catalog(adapter, _policy())

    sampled = adapter.sampled_columns()
    for column in ("email", "phone", "password_hash", "first_name"):
        assert ("public.users", column) not in sampled
        profile = catalog.profile("public.users")
        assert profile is not None
        entry = profile.column(column)
        assert entry is not None
        assert entry.sampled is False
        assert entry.values is None
        assert "denylist" in entry.reason
    assert ("public.carts", "session_token") not in sampled
    assert ("public.addresses", "line1") not in sampled


def test_export_granted_columns_never_queried() -> None:
    adapter = FakeAdapter(shop_tables())
    policy = _policy(export_granted_columns={"public.addresses.country_code"})

    catalog = collect_catalog(adapter, policy)

    assert ("public.addresses", "country_code") not in adapter.sampled_columns()
    assert catalog.sample_values("public.addresses", "country_code") == ()


def test_sampling_disabled_reads_no_values() -> None:
    adapter = FakeAdapter(shop_tables())

    catalog = collect_catalog(adapter, _policy(enabled=False))

    assert adapter.sampled_columns() == set()
    assert all(not c.sampled for p in catalog.profiles for c in p.columns)
    assert all(p.key_examples == () for p in catalog.profiles)
    # Schema metadata (incl. CHECK constraint values) is still documented.
    orders = catalog.table("public.orders")
    assert orders is not None
    assert orders.column("status") is not None


def test_high_cardinality_and_near_unique_columns_not_kept() -> None:
    tables = [
        FakeTable(
            TableRef("public", "tags"),
            [col("tag_id", "numeric"), col("label"), col("color"), col("nickname")],
            ("tag_id",),
            row_count=6,
            values={
                "label": [f"l{i}" for i in range(60)],
                "color": ["red", "blue"],
                "nickname": ["a", "b", "c", "d", "e", "f"],
            },
        )
    ]

    catalog = collect_catalog(FakeAdapter(tables), _policy())

    profile = catalog.profile("public.tags")
    assert profile is not None
    label = profile.column("label")
    nickname = profile.column("nickname")
    assert label is not None
    assert nickname is not None
    assert label.values is None
    assert "cardinality" in label.reason
    assert nickname.values is None
    assert "unique" in nickname.reason
    assert catalog.sample_values("public.tags", "color") == ("red", "blue")


def test_key_examples_for_dimension_tables_but_never_user_table() -> None:
    adapter = FakeAdapter(shop_tables())

    catalog = collect_catalog(adapter, _policy())

    products = catalog.profile("public.products")
    users = catalog.profile("public.users")
    assert products is not None
    assert users is not None
    assert products.key_examples == ("12",)  # smallest by the adapter's ordering
    assert users.key_examples == ()
    assert ("public.users", "user_id") not in adapter.sampled_columns()


def test_configured_user_table_overrides_detection() -> None:
    adapter = FakeAdapter(shop_tables())

    catalog = collect_catalog(adapter, _policy(), user_table="public.categories")

    categories = catalog.profile("public.categories")
    assert categories is not None
    assert categories.key_examples == ()


def test_schema_filter() -> None:
    tables = [*shop_tables(), FakeTable(TableRef("staging", "raw"), [col("status")])]
    adapter = FakeAdapter(tables)

    catalog = collect_catalog(adapter, _policy(), schemas=["staging"])

    assert [t.qualified_name for t in catalog.tables] == ["staging.raw"]


def test_column_sampling_timeout_is_recorded_not_fatal() -> None:
    adapter = FakeAdapter(shop_tables())

    def fail(method: str, args: tuple[str, ...]) -> None:
        if method == "get_distinct_values" and args == ("public.orders", "status"):
            raise QueryTimeoutError(30)

    adapter.fail = fail

    catalog = collect_catalog(adapter, _policy())

    profile = catalog.profile("public.orders")
    assert profile is not None
    status = profile.column("status")
    assert status is not None
    assert status.values is None
    assert "failed" in status.reason


def test_unreachable_warehouse_fails_the_crawl() -> None:
    adapter = FakeAdapter(shop_tables())

    def fail(method: str, args: tuple[str, ...]) -> None:
        if method == "describe_table" and args == ("public.orders",):
            raise WarehouseUnavailableError("down")

    adapter.fail = fail

    with pytest.raises(WarehouseUnavailableError):
        collect_catalog(adapter, _policy())


def test_collection_is_deterministic() -> None:
    first = collect_catalog(FakeAdapter(shop_tables()), _policy())
    second = collect_catalog(FakeAdapter(shop_tables()), _policy())

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_detect_user_table() -> None:
    catalog = collect_catalog(FakeAdapter(shop_tables()), _policy(enabled=False))

    detected = detect_user_table(catalog.tables)
    assert detected is not None
    assert detected.qualified_name == "public.users"
    configured = detect_user_table(catalog.tables, "public.carts")
    assert configured is not None
    assert configured.qualified_name == "public.carts"
    assert detect_user_table(catalog.tables, "public.nope") is None
    no_users = [t for t in catalog.tables if t.name != "users"]
    assert detect_user_table(no_users) is None


def test_missing_configured_schema_fails_before_anything_is_read() -> None:
    adapter = FakeAdapter(shop_tables())

    with pytest.raises(CrawlScopeError, match="nope"):
        collect_catalog(adapter, _policy(), schemas=["public", "nope"])

    assert not [c for c in adapter.calls if c[0] == "describe_table"]


def test_empty_catalog_fails() -> None:
    tables = [FakeTable(TableRef("other", "x"), [col("status")])]

    with pytest.raises(CrawlScopeError, match="no tables"):
        collect_catalog(FakeAdapter([]), _policy())
    with pytest.raises(CrawlScopeError, match="other"):
        # The schema exists but the role can read none of its tables.
        collect_catalog(_EmptySchemaAdapter(tables), _policy(), schemas=["other"])


class _EmptySchemaAdapter(FakeAdapter):
    def list_tables(self, schema: str) -> list[TableRef]:
        self._record("list_tables", schema)
        return []


def test_key_examples_only_for_template_role_tables_with_integer_keys() -> None:
    tables = [
        *shop_tables(),
        FakeTable(
            TableRef("public", "coupons"),
            [col("code", data_type="text")],
            ("code",),
            values={"code": ["SECRET10"]},
        ),
        FakeTable(
            TableRef("public", "promo_redemptions"),
            [col("id", "numeric")],
            ("id",),
            values={"id": ["5"]},
        ),
    ]
    adapter = FakeAdapter(tables)

    catalog = collect_catalog(adapter, _policy())

    examples = {c[1] for c in adapter.calls if c[0] == "get_key_examples"}
    assert examples == {"public.products", "public.categories"}
    for name in ("coupons", "promo_redemptions", "orders", "carts", "addresses"):
        profile = catalog.profile(f"public.{name}")
        assert profile is not None
        assert profile.key_examples == ()
    assert "SECRET10" not in catalog.model_dump_json()


def test_string_keyed_product_table_gets_no_key_examples() -> None:
    tables = [
        FakeTable(TableRef("public", "users"), [col("user_id", "numeric")], ("user_id",)),
        FakeTable(
            TableRef("public", "products"),
            [col("sku", data_type="text")],
            ("sku",),
            values={"sku": ["SKU-1"]},
        ),
    ]
    adapter = FakeAdapter(tables)

    catalog = collect_catalog(adapter, _policy())

    assert not [c for c in adapter.calls if c[0] == "get_key_examples"]
    profile = catalog.profile("public.products")
    assert profile is not None
    assert profile.key_examples == ()


def test_unknown_row_count_is_not_sampled() -> None:
    tables = [
        FakeTable(
            TableRef("public", "people_view", kind="view"),
            [col("nickname")],
            row_count=None,
            values={"nickname": ["a", "b", "c"]},
        )
    ]
    adapter = FakeAdapter(tables)

    catalog = collect_catalog(adapter, _policy())

    profile = catalog.profile("public.people_view")
    assert profile is not None
    entry = profile.column("nickname")
    assert entry is not None
    assert entry.values is None
    assert "row count unknown" in entry.reason
    assert ("public.people_view", "nickname") not in adapter.sampled_columns()
