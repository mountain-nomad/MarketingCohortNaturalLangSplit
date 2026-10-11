"""Upgrade stored ``draft-0`` specs to v1 (crawler ruling R1, ruling C2).

``draft-0`` was the crawler's placeholder. The mapping is mechanical:

* ``entity {table, key}`` is dropped: v1's entity is always the user, identified by the
  business-context canonical user id (S2); paths still start at the user table;
* ``related`` gets ``table`` = the first hop's table (the table directly linked to the user);
* time windows ``{column, last_days}`` become ``{"kind": "relative", ...}``;
* every other node keeps its shape.

:func:`load_spec` is the one reader for stored specs: it upgrades ``draft-0`` on read, so a
row written by an older crawler binary still compiles, and refuses unknown versions.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from cohortsplit.cohort_spec import draft
from cohortsplit.cohort_spec.common import Filter, table_of
from cohortsplit.cohort_spec.v1 import (
    SPEC_VERSION,
    AllOf,
    AnyOf,
    AttributeCondition,
    AttributeTimeWindowCondition,
    CohortSpecV1,
    Condition,
    Join,
    Not,
    RelatedCondition,
    RelativeWindow,
)
from cohortsplit.cohort_spec.v1 import Aggregate as V1Aggregate

DRAFT_VERSION = "draft-0"


class SpecVersionError(ValueError):
    """The stored document has an unknown ``spec_version``."""


def _window(window: draft.TimeWindow | None) -> RelativeWindow | None:
    if window is None:
        return None
    return RelativeWindow(column=window.column, last_days=window.last_days)


def _filter(value_filter: draft.ValueFilter) -> Filter:
    return Filter(
        column=value_filter.column, operator=value_filter.operator, value=value_filter.value
    )


def _condition(condition: draft.Condition) -> Condition:
    match condition:
        case draft.AttributeCondition(filter=value_filter):
            return AttributeCondition(filter=_filter(value_filter))
        case draft.AttributeTimeWindowCondition(window=window):
            upgraded = _window(window)
            assert upgraded is not None  # noqa: S101 (required in draft-0)
            return AttributeTimeWindowCondition(window=upgraded)
        case draft.RelatedCondition():
            aggregate = condition.aggregate
            return RelatedCondition(
                table=table_of(condition.path[0].to_column),
                path=tuple(
                    Join(from_column=j.from_column, to_column=j.to_column) for j in condition.path
                ),
                filters=tuple(_filter(f) for f in condition.filters),
                time_window=_window(condition.time_window),
                aggregate=(
                    None
                    if aggregate is None
                    else V1Aggregate(
                        function=aggregate.function,
                        column=aggregate.column if aggregate.function == "sum" else None,
                        operator=aggregate.operator,
                        value=aggregate.value,
                    )
                ),
            )
        case draft.AllOf(conditions=children):
            return AllOf(conditions=tuple(_condition(c) for c in children))
        case draft.AnyOf(conditions=children):
            return AnyOf(conditions=tuple(_condition(c) for c in children))
        case draft.Not(condition=child):
            return Not(condition=_condition(child))
    raise TypeError(f"unknown draft condition {type(condition).__name__}")  # pragma: no cover


def upgrade_draft0(spec: draft.DraftCohortSpec) -> CohortSpecV1:
    return CohortSpecV1(spec_version=SPEC_VERSION, where=_condition(spec.where))


def load_spec(raw: Mapping[str, Any]) -> CohortSpecV1:
    """A stored or submitted spec as v1 (``draft-0`` upgraded). Raises ValueError subclasses:
    :class:`SpecVersionError` for unknown versions, ``ValidationError`` for invalid ones."""
    version = raw.get("spec_version")
    if version == SPEC_VERSION:
        return CohortSpecV1.model_validate(dict(raw))
    if version == DRAFT_VERSION:
        return upgrade_draft0(draft.DraftCohortSpec.model_validate(dict(raw)))
    raise SpecVersionError(f"unknown spec_version {version!r}")


def try_load_spec(raw: Mapping[str, Any]) -> CohortSpecV1 | None:
    try:
        return load_spec(raw)
    except (SpecVersionError, ValidationError):
        return None
