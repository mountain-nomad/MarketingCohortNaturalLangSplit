"""In-memory :class:`WarehouseAdapter` for unit tests (records every call)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from cohortsplit.warehouse.models import (
    ColumnInfo,
    ForeignKey,
    QueryResult,
    TableMetadata,
    TableRef,
    TypeCategory,
)


def col(
    name: str,
    category: TypeCategory = "string",
    *,
    data_type: str | None = None,
    allowed: tuple[str, ...] | None = None,
    nullable: bool = True,
) -> ColumnInfo:
    default_types = {
        "string": "text",
        "boolean": "boolean",
        "enum": "mood",
        "numeric": "bigint",
        "datetime": "timestamp with time zone",
        "other": "jsonb",
    }
    return ColumnInfo(
        name=name,
        data_type=data_type or default_types[category],
        type_category=category,
        nullable=nullable,
        ordinal=0,
        allowed_values=allowed,
    )


def fk(
    column: str, table: str, ref_column: str | None = None, schema: str = "public"
) -> ForeignKey:
    return ForeignKey(
        name=f"{column}_fkey",
        columns=(column,),
        referred_schema=schema,
        referred_table=table,
        referred_columns=(ref_column or column,),
    )


@dataclass
class FakeTable:
    ref: TableRef
    columns: list[ColumnInfo]
    primary_key: tuple[str, ...] = ()
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    row_count: int | None = 100
    # Distinct non-null values per column, most frequent first.
    values: dict[str, list[str]] = field(default_factory=dict)


class FakeAdapter:
    dialect = "postgresql"

    def __init__(self, tables: list[FakeTable]) -> None:
        self.tables = {t.ref.qualified_name: t for t in tables}
        self.calls: list[tuple[str, ...]] = []
        self.fail: Callable[[str, tuple[str, ...]], None] | None = None

    def _record(self, *call: str) -> None:
        self.calls.append(call)
        if self.fail is not None:
            self.fail(call[0], call[1:])

    def describe_location(self) -> str:
        return "fake:5432/shop"

    def test_connection(self) -> None:
        self._record("test_connection")

    def list_schemas(self) -> list[str]:
        self._record("list_schemas")
        return sorted({t.ref.schema for t in self.tables.values()})

    def list_tables(self, schema: str) -> list[TableRef]:
        self._record("list_tables", schema)
        return sorted(t.ref for t in self.tables.values() if t.ref.schema == schema)

    def describe_table(self, table: TableRef) -> TableMetadata:
        self._record("describe_table", table.qualified_name)
        t = self.tables[table.qualified_name]
        return TableMetadata(
            ref=t.ref,
            columns=tuple(replace(c, ordinal=i + 1) for i, c in enumerate(t.columns)),
            primary_key=t.primary_key,
            foreign_keys=tuple(t.foreign_keys),
            estimated_row_count=t.row_count,
        )

    def get_distinct_values(
        self, table: TableRef, column: str, max_distinct: int
    ) -> list[str] | None:
        self._record("get_distinct_values", table.qualified_name, column)
        values = self.tables[table.qualified_name].values.get(column, [])
        return None if len(values) > max_distinct else list(values)

    def get_key_examples(self, table: TableRef, column: str, limit: int) -> list[str]:
        self._record("get_key_examples", table.qualified_name, column)
        values = self.tables[table.qualified_name].values.get(column, [])
        return sorted(values)[:limit]

    def execute_readonly(self, sql: str, params: Mapping[str, object] | None = None) -> QueryResult:
        self._record("execute_readonly", sql)
        return QueryResult(columns=(), rows=())

    def sampled_columns(self) -> set[tuple[str, str]]:
        return {
            (c[1], c[2]) for c in self.calls if c[0] in ("get_distinct_values", "get_key_examples")
        }


def _t(name: str, schema: str = "public") -> TableRef:
    return TableRef(schema=schema, name=name)


def shop_tables(*, with_likes: bool = False, with_referral: bool = False) -> list[FakeTable]:
    """A small ecommerce-shaped warehouse resembling the demo `ecommerce` schema."""
    users_cols = [
        col("user_id", "numeric", nullable=False),
        col("email", data_type="citext"),
        col("phone"),
        col("password_hash"),
        col("first_name"),
        col("is_active", "boolean"),
        col("created_at", "datetime"),
    ]
    if with_referral:
        users_cols.append(col("referral_code"))
    tables = [
        FakeTable(
            _t("users"),
            users_cols,
            ("user_id",),
            row_count=91,
            values={
                "user_id": [str(i) for i in range(1, 92)],
                "email": ["a@example.com"],
                "phone": ["+100"],
                "password_hash": ["x"],
                "first_name": ["Ann", "Bob"],
                "is_active": ["true", "false"],
                "referral_code": ["GET100", "SPRING"],
            },
        ),
        FakeTable(
            _t("addresses"),
            [
                col("address_id", "numeric"),
                col("user_id", "numeric"),
                col("line1"),
                col("country_code", data_type="character(2)"),
            ],
            ("address_id",),
            [fk("user_id", "users")],
            row_count=91,
            values={"country_code": ["KZ", "US", "DE"], "line1": ["1 Main St"]},
        ),
        FakeTable(
            _t("categories"),
            [col("category_id", "numeric"), col("name")],
            ("category_id",),
            row_count=8,
            values={"category_id": ["1", "2", "3"], "name": ["Beverages", "Seafood", "Produce"]},
        ),
        FakeTable(
            _t("products"),
            [col("product_id", "numeric"), col("category_id", "numeric"), col("name")],
            ("product_id",),
            [fk("category_id", "categories")],
            row_count=77,
            values={"product_id": ["7", "3", "12"]},
        ),
        FakeTable(
            _t("product_variants"),
            [col("variant_id", "numeric"), col("product_id", "numeric"), col("sku")],
            ("variant_id",),
            [fk("product_id", "products")],
            row_count=77,
        ),
        FakeTable(
            _t("orders"),
            [
                col("order_id", "numeric"),
                col("user_id", "numeric"),
                col(
                    "status",
                    allowed=("pending", "paid", "processing", "shipped", "delivered", "cancelled"),
                ),
                col("grand_total", "numeric", data_type="numeric(12,2)"),
                col("ordered_at", "datetime"),
                col("created_at", "datetime"),
            ],
            ("order_id",),
            [fk("user_id", "users")],
            row_count=830,
            values={"status": ["delivered", "processing", "pending", "cancelled"]},
        ),
        FakeTable(
            _t("order_items"),
            [
                col("order_id", "numeric"),
                col("variant_id", "numeric"),
                col("discount", "numeric", data_type="numeric(5,4)"),
            ],
            ("order_id", "variant_id"),
            [fk("order_id", "orders"), fk("variant_id", "product_variants")],
            row_count=2155,
        ),
        FakeTable(
            _t("inventory_movements"),
            [
                col("movement_id", "numeric"),
                col("variant_id", "numeric"),
                col("order_id", "numeric"),
            ],
            ("movement_id",),
            [fk("variant_id", "product_variants"), fk("order_id", "orders")],
            row_count=69,
        ),
        FakeTable(
            _t("carts"),
            [
                col("cart_id", "numeric"),
                col("user_id", "numeric"),
                col("session_token", data_type="uuid"),
                col("status", allowed=("active", "converted", "abandoned")),
            ],
            ("cart_id",),
            [fk("user_id", "users")],
            row_count=10,
            values={"status": ["active"], "session_token": ["tok"]},
        ),
        FakeTable(
            _t("cart_items"),
            [col("cart_id", "numeric"), col("variant_id", "numeric")],
            ("cart_id", "variant_id"),
            [fk("cart_id", "carts"), fk("variant_id", "product_variants")],
            row_count=20,
        ),
    ]
    if with_likes:
        tables.append(
            FakeTable(
                _t("product_likes"),
                [
                    col("like_id", "numeric"),
                    col("user_id", "numeric"),
                    col("product_id", "numeric"),
                ],
                ("like_id",),
                [fk("user_id", "users"), fk("product_id", "products")],
                row_count=40,
            )
        )
    return tables
