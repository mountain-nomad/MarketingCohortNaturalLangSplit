"""References in business context and edited use cases must exist in the latest crawl (FR-3)."""

from typing import Any

from cohortsplit.cohort_spec.draft import DraftCohortSpec
from cohortsplit.semantic.context import BusinessContextIn
from cohortsplit.semantic.inventory import (
    ReferenceProblem,
    SchemaInventory,
    TableShape,
    check_definition,
    check_spec,
)


def table_doc(schema: str, name: str, columns: list[str], pk: list[str]) -> dict[str, Any]:
    return {
        "schema_name": schema,
        "name": name,
        "kind": "table",
        "comment": None,
        "columns": [
            {
                "name": c,
                "data_type": "text",
                "type_category": "string",
                "nullable": True,
                "comment": None,
                "allowed_values": None,
            }
            for c in columns
        ],
        "primary_key": pk,
        "foreign_keys": [],
    }


def inventory() -> SchemaInventory:
    return SchemaInventory.from_table_docs(
        [
            table_doc("public", "users", ["user_id", "email", "deleted_at"], ["user_id"]),
            table_doc(
                "public", "orders", ["order_id", "user_id", "status", "ordered_at"], ["order_id"]
            ),
            table_doc(
                "public", "order_items", ["order_id", "variant_id"], ["order_id", "variant_id"]
            ),
        ]
    )


def defn(fields: dict[str, Any]) -> Any:
    return BusinessContextIn.model_validate({"key": "k", "definition": fields}).definition


def test_inventory_built_from_generated_table_docs() -> None:
    assert inventory().tables["public.users"] == TableShape(
        columns=frozenset({"user_id", "email", "deleted_at"}), primary_key=("user_id",)
    )
    assert inventory().has_table("public.orders")
    assert not inventory().has_table("public.likes")
    assert inventory().has_column("public.orders.status")
    assert not inventory().has_column("public.orders.discount")
    assert not inventory().has_column("public.likes.user_id")


def test_resolvable_definition_has_no_problems() -> None:
    metric = defn(
        {
            "kind": "metric",
            "table": "public.orders",
            "filters": [{"column": "public.orders.status", "operator": "=", "value": "paid"}],
            "time_column": "public.orders.ordered_at",
        }
    )

    assert check_definition(metric, inventory()) == ()


def test_unknown_table_reported_once() -> None:
    metric = defn(
        {
            "kind": "metric",
            "table": "public.likes",
            "filters": [{"column": "public.likes.product_id", "operator": "=", "value": 7}],
        }
    )

    assert check_definition(metric, inventory()) == (
        ReferenceProblem("public.likes", "unknown table"),
    )


def test_unknown_column_reported() -> None:
    exclusion = defn(
        {
            "kind": "exclusion",
            "table": "public.users",
            "filters": [
                {"column": "public.users.is_deleted", "operator": "=", "value": True},
                {"column": "public.users.deleted_at", "operator": "is_not_null"},
            ],
        }
    )

    assert check_definition(exclusion, inventory()) == (
        ReferenceProblem("public.users.is_deleted", "unknown column"),
    )


def test_problems_are_sorted_for_deterministic_errors() -> None:
    term = defn({"kind": "term", "column": "public.users.zzz"})
    status = defn(
        {"kind": "status_semantics", "column": "public.carts.status", "meanings": {"a": "b"}}
    )

    assert check_definition(term, inventory()) == (
        ReferenceProblem("public.users.zzz", "unknown column"),
    )
    assert check_definition(status, inventory()) == (
        ReferenceProblem("public.carts", "unknown table"),
    )


def test_canonical_user_id_must_be_single_column_primary_key() -> None:
    assert (
        check_definition(
            defn({"kind": "canonical_user_id", "column": "public.users.user_id"}), inventory()
        )
        == ()
    )
    assert check_definition(
        defn({"kind": "canonical_user_id", "column": "public.users.email"}), inventory()
    ) == (ReferenceProblem("public.users.email", "not the single-column primary key of its table"),)
    assert check_definition(
        defn({"kind": "canonical_user_id", "column": "public.order_items.order_id"}), inventory()
    ) == (
        ReferenceProblem(
            "public.order_items.order_id", "not the single-column primary key of its table"
        ),
    )


def test_time_window_without_column_always_resolves() -> None:
    assert check_definition(defn({"kind": "time_window", "last_days": 7}), inventory()) == ()
    assert SchemaInventory.from_table_docs([]).tables == {}


def spec(column: str) -> DraftCohortSpec:
    return DraftCohortSpec.model_validate(
        {
            "entity": {"table": "public.users", "key": "user_id"},
            "where": {
                "type": "related",
                "path": [
                    {"from_column": "public.users.user_id", "to_column": "public.orders.user_id"}
                ],
                "filters": [{"column": column, "operator": "=", "value": "paid"}],
            },
        }
    )


def test_check_spec_reports_missing_columns() -> None:
    assert check_spec(spec("public.orders.status"), inventory()) == ()
    assert check_spec(spec("public.orders.state"), inventory()) == (
        ReferenceProblem("public.orders.state", "unknown column"),
    )
