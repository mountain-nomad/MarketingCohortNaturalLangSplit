"""Cohort spec v1: typed, versioned, strict, hashable (FR-4, BR-6, ruling C1)."""

from typing import Any

import pytest
from pydantic import ValidationError

from cohortsplit.cohort_spec.v1 import (
    MAX_CHILDREN,
    MAX_DEPTH,
    SPEC_VERSION,
    AbsoluteWindow,
    CohortSpecV1,
    MetricCondition,
    RelatedCondition,
    RelativeWindow,
    referenced_columns,
    referenced_tables,
    referenced_terms,
    spec_hash,
)

ORDERS_PATH = [{"from_column": "public.users.user_id", "to_column": "public.orders.user_id"}]

SPEC: dict[str, Any] = {
    "spec_version": "1",
    "entity": "user",
    "where": {
        "type": "all_of",
        "conditions": [
            {
                "type": "attribute",
                "filter": {"column": "public.users.is_active", "operator": "=", "value": True},
            },
            {
                "type": "related",
                "table": "public.orders",
                "path": ORDERS_PATH,
                "filters": [
                    {"column": "public.orders.status", "operator": "=", "value": "delivered"}
                ],
                "time_window": {
                    "kind": "relative",
                    "column": "public.orders.ordered_at",
                    "last_days": 90,
                },
                "aggregate": {"function": "count", "operator": ">=", "value": 2},
            },
            {
                "type": "not",
                "condition": {
                    "type": "metric",
                    "term": "purchase",
                    "time_window": {"kind": "term", "term": "recently"},
                },
            },
            {
                "type": "any_of",
                "conditions": [
                    {
                        "type": "attribute_time_window",
                        "window": {
                            "kind": "absolute",
                            "column": "public.users.created_at",
                            "start": "2024-01-01T00:00:00Z",
                            "end": "2025-01-01T00:00:00Z",
                        },
                    },
                    {
                        "type": "related",
                        "table": "public.carts",
                        "filters": [
                            {"column": "public.carts.status", "operator": "in", "value": ["a"]}
                        ],
                    },
                ],
            },
        ],
    },
}


def test_version_marker_and_entity() -> None:
    spec = CohortSpecV1.model_validate(SPEC)

    assert SPEC_VERSION == "1"
    assert spec.spec_version == "1"
    assert spec.entity == "user"


def test_json_round_trip_is_stable() -> None:
    spec = CohortSpecV1.model_validate(SPEC)

    dumped = spec.model_dump(mode="json", exclude_none=True)

    assert CohortSpecV1.model_validate(dumped) == spec
    assert dumped["spec_version"] == "1"


def test_where_may_be_omitted_for_every_user() -> None:
    assert CohortSpecV1.model_validate({"spec_version": "1"}).where is None


@pytest.mark.parametrize("version", ["draft-0", "2", None])
def test_other_versions_rejected(version: object) -> None:
    with pytest.raises(ValidationError):
        CohortSpecV1.model_validate({**SPEC, "spec_version": version})


def test_entity_other_than_user_rejected() -> None:
    with pytest.raises(ValidationError):
        CohortSpecV1.model_validate({**SPEC, "entity": "order"})


@pytest.mark.parametrize(
    "where",
    [
        # BR-6: ordered sequences have no representation.
        {"type": "sequence", "steps": []},
        {"type": "related", "table": "public.orders", "then": {"type": "related"}},
        # No SQL anywhere.
        {
            "type": "attribute",
            "filter": {"column": "public.users.x", "operator": "=", "value": 1},
            "sql": "1=1",
        },
        {"type": "sql", "sql": "SELECT 1"},
    ],
    ids=["sequence-node", "then-field", "sql-field", "sql-node"],
)
def test_unsupported_constructs_and_sql_are_rejected(where: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CohortSpecV1.model_validate({"spec_version": "1", "where": where})


def test_extra_top_level_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        CohortSpecV1.model_validate({**SPEC, "sql": "SELECT 1"})


@pytest.mark.parametrize(
    "column",
    ["users.is_active", "is_active", "public.users", "public.users.is active", ""],
)
def test_columns_must_be_qualified(column: str) -> None:
    where = {"type": "attribute", "filter": {"column": column, "operator": "=", "value": 1}}
    with pytest.raises(ValidationError):
        CohortSpecV1.model_validate({"spec_version": "1", "where": where})


@pytest.mark.parametrize(
    ("operator", "value"),
    [("in", "x"), ("in", []), ("=", ["a", "b"]), ("is_null", 1), ("=", None), ("like", "x")],
)
def test_filter_value_must_match_operator(operator: str, value: object) -> None:
    where = {
        "type": "attribute",
        "filter": {"column": "public.users.x", "operator": operator, "value": value},
    }
    with pytest.raises(ValidationError):
        CohortSpecV1.model_validate({"spec_version": "1", "where": where})


def test_related_target_must_be_on_its_path() -> None:
    where = {"type": "related", "table": "public.carts", "path": ORDERS_PATH}
    with pytest.raises(ValidationError, match="path"):
        CohortSpecV1.model_validate({"spec_version": "1", "where": where})


def test_related_path_is_optional() -> None:
    spec = CohortSpecV1.model_validate(
        {"spec_version": "1", "where": {"type": "related", "table": "public.orders"}}
    )
    assert isinstance(spec.where, RelatedCondition)
    assert spec.where.path is None


def test_count_aggregate_takes_no_column() -> None:
    where = {
        "type": "related",
        "table": "public.orders",
        "aggregate": {
            "function": "count",
            "column": "public.orders.grand_total",
            "operator": ">",
            "value": 1,
        },
    }
    with pytest.raises(ValidationError):
        CohortSpecV1.model_validate({"spec_version": "1", "where": where})


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_aggregate_threshold_must_be_finite(value: float) -> None:
    where = {
        "type": "metric",
        "term": "spent",
        "aggregate": {"function": "sum", "operator": ">", "value": value},
    }
    with pytest.raises(ValidationError):
        CohortSpecV1.model_validate({"spec_version": "1", "where": where})


def test_absolute_window_normalized_to_utc() -> None:
    where = {
        "type": "attribute_time_window",
        "window": {
            "kind": "absolute",
            "column": "public.users.created_at",
            "start": "2024-01-01",
            "end": "2025-01-01T05:00:00+05:00",
        },
    }
    spec = CohortSpecV1.model_validate({"spec_version": "1", "where": where})

    window = spec.model_dump(mode="json")["where"]["window"]
    assert window["start"] == "2024-01-01T00:00:00Z"
    assert window["end"] == "2025-01-01T00:00:00Z"
    assert isinstance(spec.where.window, AbsoluteWindow)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "window",
    [
        {"kind": "absolute", "column": "public.users.created_at"},
        {
            "kind": "absolute",
            "column": "public.users.created_at",
            "start": "2025-01-01",
            "end": "2024-01-01",
        },
        {"kind": "relative", "column": "public.users.created_at", "last_days": 0},
        {"column": "public.users.created_at", "last_days": 30},
    ],
    ids=["no-bounds", "start-after-end", "zero-days", "no-kind"],
)
def test_invalid_windows_rejected(window: dict[str, Any]) -> None:
    where = {"type": "attribute_time_window", "window": window}
    with pytest.raises(ValidationError):
        CohortSpecV1.model_validate({"spec_version": "1", "where": where})


def test_metric_condition_references_a_term() -> None:
    spec = CohortSpecV1.model_validate(
        {"spec_version": "1", "where": {"type": "metric", "term": "Purchase"}}
    )
    assert isinstance(spec.where, MetricCondition)
    assert spec.where.term == "Purchase"


def _nested(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "attribute",
        "filter": {"column": "public.users.x", "operator": "=", "value": 1},
    }
    for _ in range(depth - 1):
        node = {"type": "not", "condition": node}
    return node


def test_depth_limit() -> None:
    CohortSpecV1.model_validate({"spec_version": "1", "where": _nested(MAX_DEPTH)})
    with pytest.raises(ValidationError, match="deep"):
        CohortSpecV1.model_validate({"spec_version": "1", "where": _nested(MAX_DEPTH + 1)})


def test_children_limit() -> None:
    child = _nested(1)
    with pytest.raises(ValidationError):
        CohortSpecV1.model_validate(
            {
                "spec_version": "1",
                "where": {"type": "any_of", "conditions": [child] * (MAX_CHILDREN + 1)},
            }
        )


def test_referenced_columns_tables_and_terms() -> None:
    spec = CohortSpecV1.model_validate(SPEC)

    assert referenced_columns(spec) == {
        "public.users.is_active",
        "public.users.user_id",
        "public.orders.user_id",
        "public.orders.status",
        "public.orders.ordered_at",
        "public.users.created_at",
        "public.carts.status",
    }
    assert referenced_tables(spec) == {"public.users", "public.orders", "public.carts"}
    assert referenced_terms(spec) == {"purchase", "recently"}


def test_spec_hash_is_deterministic_and_content_sensitive() -> None:
    spec = CohortSpecV1.model_validate(SPEC)
    reordered = CohortSpecV1.model_validate(dict(reversed(list(SPEC.items()))))
    changed = CohortSpecV1.model_validate(
        {"spec_version": "1", "where": {"type": "metric", "term": "purchase"}}
    )

    assert spec_hash(spec) == spec_hash(reordered)
    assert len(spec_hash(spec)) == 64
    assert spec_hash(spec) != spec_hash(changed)


def test_relative_window_defaults_column_to_none() -> None:
    window = RelativeWindow(last_days=7)
    assert window.column is None
    assert window.kind == "relative"
