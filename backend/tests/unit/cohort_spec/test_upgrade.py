"""draft-0 -> v1 upgrade (crawler ruling R1, ruling C2)."""

from typing import Any

import pytest

from cohortsplit.cohort_spec.draft import DraftCohortSpec
from cohortsplit.cohort_spec.draft import referenced_columns as draft_columns
from cohortsplit.cohort_spec.upgrade import SpecVersionError, load_spec, upgrade_draft0
from cohortsplit.cohort_spec.v1 import CohortSpecV1, referenced_columns
from tests.unit.cohort_spec.test_draft import SPEC as DRAFT_SPEC

EXPECTED_V1: dict[str, Any] = {
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
                "type": "not",
                "condition": {
                    "type": "related",
                    # The target is the table directly linked to the user (first hop).
                    "table": "public.orders",
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
                    "time_window": {
                        "kind": "relative",
                        "column": "public.orders.ordered_at",
                        "last_days": 90,
                    },
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
                        "window": {
                            "kind": "relative",
                            "column": "public.users.created_at",
                            "last_days": 30,
                        },
                    }
                ],
            },
        ],
    },
}


def test_upgrade_maps_every_draft_node() -> None:
    upgraded = upgrade_draft0(DraftCohortSpec.model_validate(DRAFT_SPEC))

    assert upgraded.model_dump(mode="json", exclude_none=True) == EXPECTED_V1


def test_upgrade_keeps_referenced_columns_for_rereview_flagging() -> None:
    draft = DraftCohortSpec.model_validate(DRAFT_SPEC)

    assert referenced_columns(upgrade_draft0(draft)) == draft_columns(draft)


def test_load_spec_upgrades_draft0_on_read() -> None:
    assert load_spec(DRAFT_SPEC) == CohortSpecV1.model_validate(EXPECTED_V1)


def test_load_spec_accepts_v1() -> None:
    assert load_spec(EXPECTED_V1) == CohortSpecV1.model_validate(EXPECTED_V1)


@pytest.mark.parametrize("version", ["v9", None, 1])
def test_load_spec_refuses_unknown_versions(version: object) -> None:
    with pytest.raises(SpecVersionError):
        load_spec({**EXPECTED_V1, "spec_version": version})


def test_attribute_only_draft_upgrades_without_entity() -> None:
    draft = DraftCohortSpec.model_validate(
        {
            "spec_version": "draft-0",
            "entity": {"table": "public.users", "key": "user_id"},
            "where": {
                "type": "attribute",
                "filter": {"column": "public.users.referral_code", "operator": "=", "value": "X"},
            },
        }
    )

    upgraded = upgrade_draft0(draft)

    assert upgraded.model_dump(mode="json", exclude_none=True) == {
        "spec_version": "1",
        "entity": "user",
        "where": {
            "type": "attribute",
            "filter": {"column": "public.users.referral_code", "operator": "=", "value": "X"},
        },
    }
