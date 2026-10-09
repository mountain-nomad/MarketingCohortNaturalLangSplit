"""Draft cohort spec ``draft-0``: versioned, strict, and introspectable for AC-26."""

import pytest
from pydantic import ValidationError

from cohortsplit.cohort_spec.draft import (
    SPEC_VERSION,
    DraftCohortSpec,
    referenced_columns,
    referenced_tables,
)

SPEC = {
    "spec_version": "draft-0",
    "entity": {"table": "public.users", "key": "user_id"},
    "where": {
        "type": "all_of",
        "conditions": [
            {
                "type": "attribute",
                "filter": {"column": "public.users.is_active", "operator": "=", "value": True},
            },
            {
                "type": "not",
                "condition": {
                    "type": "related",
                    "path": [
                        {
                            "from_column": "public.users.user_id",
                            "to_column": "public.orders.user_id",
                        },
                        {
                            "from_column": "public.orders.order_id",
                            "to_column": "public.order_items.order_id",
                        },
                    ],
                    "filters": [
                        {"column": "public.order_items.discount", "operator": ">", "value": 0}
                    ],
                    "time_window": {"column": "public.orders.ordered_at", "last_days": 90},
                    "aggregate": {
                        "function": "sum",
                        "column": "public.orders.grand_total",
                        "operator": ">",
                        "value": 500,
                    },
                },
            },
            {
                "type": "any_of",
                "conditions": [
                    {
                        "type": "attribute_time_window",
                        "window": {"column": "public.users.created_at", "last_days": 30},
                    }
                ],
            },
        ],
    },
}


def test_version_marker() -> None:
    assert SPEC_VERSION == "draft-0"
    assert DraftCohortSpec.model_validate(SPEC).spec_version == "draft-0"


def test_json_round_trip_is_stable() -> None:
    spec = DraftCohortSpec.model_validate(SPEC)

    dumped = spec.model_dump(mode="json")

    assert DraftCohortSpec.model_validate(dumped) == spec
    assert dumped["spec_version"] == "draft-0"
    assert spec.model_dump_json() == DraftCohortSpec.model_validate(dumped).model_dump_json()


def test_other_versions_rejected() -> None:
    with pytest.raises(ValidationError):
        DraftCohortSpec.model_validate({**SPEC, "spec_version": "1"})


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        DraftCohortSpec.model_validate({**SPEC, "sql": "SELECT 1"})


@pytest.mark.parametrize("bad", ["users.email", "email", "a.b.c.d", "public.users. x"])
def test_columns_must_be_qualified(bad: str) -> None:
    with pytest.raises(ValidationError):
        DraftCohortSpec.model_validate(
            {
                "entity": {"table": "public.users", "key": "user_id"},
                "where": {
                    "type": "attribute",
                    "filter": {"column": bad, "operator": "=", "value": "x"},
                },
            }
        )


def test_referenced_columns_and_tables() -> None:
    spec = DraftCohortSpec.model_validate(SPEC)

    assert referenced_columns(spec) == {
        "public.users.user_id",
        "public.users.is_active",
        "public.users.created_at",
        "public.orders.user_id",
        "public.orders.order_id",
        "public.orders.ordered_at",
        "public.orders.grand_total",
        "public.order_items.order_id",
        "public.order_items.discount",
    }
    assert referenced_tables(spec) == {"public.users", "public.orders", "public.order_items"}
