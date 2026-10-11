"""Cohort specification v1 (FR-4, ruling C1 in docs/decisions/0003-cohort-compiler-rulings.md).

The LLM's only output that can lead to a cohort. Typed, versioned and strict
(``extra="forbid"``): there is no SQL anywhere and no way to express an ordered
sequence (BR-6). Every reference is qualified (``schema.table[.column]``) or names a
business-context term; the compiler resolves and validates all of them against the
semantic snapshot before anything is compiled.

Conditions:

* ``attribute`` — a filter on a column of the canonical user table;
* ``attribute_time_window`` — a user-table timestamp within a window ("registered in 2024");
* ``related`` — existence (or a count/sum threshold) of rows of ``table`` reached from the
  user through ``path`` (FK hops outwards from the user table; may be omitted when exactly
  one shortest FK path exists, may continue past ``table`` for filters on further tables);
* ``metric`` — the same over a business-context ``metric`` term ("purchase", "spent");
* ``all_of`` / ``any_of`` / ``not`` — nesting.

Aggregates count or sum **distinct rows of the target table**, so joins never inflate them.
"""

import hashlib
import json
import math
from datetime import UTC, datetime
from itertools import pairwise
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from cohortsplit.cohort_spec.common import (
    Filter,
    FrozenModel,
    QualifiedColumn,
    QualifiedTable,
    normalize_term,
    table_of,
)

SPEC_VERSION: Literal["1"] = "1"
MAX_DEPTH = 8
MAX_CHILDREN = 50
MAX_NODES = 200
MAX_FILTERS = 20
MAX_PATH = 6
MAX_LAST_DAYS = 36500

TermName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Join(FrozenModel):
    """One FK hop, walked from the user table outwards."""

    from_column: QualifiedColumn
    to_column: QualifiedColumn


class RelativeWindow(FrozenModel):
    """The last ``last_days`` days before execution time (UTC)."""

    kind: Literal["relative"] = "relative"
    column: QualifiedColumn | None = None
    last_days: int = Field(ge=1, le=MAX_LAST_DAYS)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)  # absolute ranges are interpreted in UTC
    return value.astimezone(UTC)


class AbsoluteWindow(FrozenModel):
    """``[start, end)`` in UTC; either bound may be open."""

    kind: Literal["absolute"] = "absolute"
    column: QualifiedColumn | None = None
    start: datetime | None = None
    end: datetime | None = None

    @field_validator("start", "end")
    @classmethod
    def _to_utc(cls, value: datetime | None) -> datetime | None:
        return _utc(value)

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if self.start is None and self.end is None:
            raise ValueError("an absolute window needs a start or an end")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class TermWindow(FrozenModel):
    """A business-context ``time_window`` term ("recently")."""

    kind: Literal["term"] = "term"
    term: TermName
    column: QualifiedColumn | None = None


TimeWindow = Annotated[RelativeWindow | AbsoluteWindow | TermWindow, Field(discriminator="kind")]

FiniteNumber = int | Annotated[float, Field(allow_inf_nan=False)]


class Aggregate(FrozenModel):
    """``count`` of distinct target rows, or ``sum`` of a target column, vs. a threshold."""

    function: Literal["count", "sum"]
    column: QualifiedColumn | None = None
    operator: Literal[">", ">=", "=", "!=", "<", "<="]
    value: FiniteNumber

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.function == "count" and self.column is not None:
            raise ValueError("count takes no column (it counts distinct target rows)")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("threshold must be finite")
        return self


Filters = Annotated[tuple[Filter, ...], Field(max_length=MAX_FILTERS)]
Path = Annotated[tuple[Join, ...], Field(min_length=1, max_length=MAX_PATH)]


def _check_path(path: tuple[Join, ...] | None) -> None:
    if path is None:
        return
    for previous, join in pairwise(path):
        if table_of(join.from_column) != table_of(previous.to_column):
            raise ValueError("path must be contiguous: each hop starts where the previous ends")


class AttributeCondition(FrozenModel):
    type: Literal["attribute"] = "attribute"
    filter: Filter


class AttributeTimeWindowCondition(FrozenModel):
    type: Literal["attribute_time_window"] = "attribute_time_window"
    window: TimeWindow


class RelatedCondition(FrozenModel):
    type: Literal["related"] = "related"
    table: QualifiedTable
    path: Path | None = None
    filters: Filters = ()
    time_window: TimeWindow | None = None
    aggregate: Aggregate | None = None

    @model_validator(mode="after")
    def _target_on_path(self) -> Self:
        _check_path(self.path)
        if self.path is not None and self.table not in {table_of(j.to_column) for j in self.path}:
            raise ValueError(f"table {self.table} must be reached by the path")
        return self


class MetricCondition(FrozenModel):
    type: Literal["metric"] = "metric"
    term: TermName
    path: Path | None = None
    filters: Filters = ()
    time_window: TimeWindow | None = None
    aggregate: Aggregate | None = None

    @model_validator(mode="after")
    def _contiguous(self) -> Self:
        _check_path(self.path)
        return self


class AllOf(FrozenModel):
    type: Literal["all_of"] = "all_of"
    conditions: Annotated[tuple["Condition", ...], Field(min_length=1, max_length=MAX_CHILDREN)]


class AnyOf(FrozenModel):
    type: Literal["any_of"] = "any_of"
    conditions: Annotated[tuple["Condition", ...], Field(min_length=1, max_length=MAX_CHILDREN)]


class Not(FrozenModel):
    type: Literal["not"] = "not"
    condition: "Condition"


Condition = Annotated[
    AttributeCondition
    | AttributeTimeWindowCondition
    | RelatedCondition
    | MetricCondition
    | AllOf
    | AnyOf
    | Not,
    Field(discriminator="type"),
]


def _children(condition: Condition) -> tuple[Condition, ...]:
    if isinstance(condition, AllOf | AnyOf):
        return condition.conditions
    if isinstance(condition, Not):
        return (condition.condition,)
    return ()


def _depth_and_size(condition: Condition) -> tuple[int, int]:
    depth, size = 1, 1
    for child in _children(condition):
        child_depth, child_size = _depth_and_size(child)
        depth = max(depth, child_depth + 1)
        size += child_size
    return depth, size


class CohortSpecV1(FrozenModel):
    spec_version: Literal["1"]
    entity: Literal["user"] = "user"
    where: Condition | None = None

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if self.where is not None:
            depth, size = _depth_and_size(self.where)
            if depth > MAX_DEPTH:
                raise ValueError(f"conditions are nested too deep (at most {MAX_DEPTH} levels)")
            if size > MAX_NODES:
                raise ValueError(f"too many conditions (at most {MAX_NODES})")
        return self


AllOf.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()
CohortSpecV1.model_rebuild()


def walk(condition: Condition | None) -> list[Condition]:
    """Every condition node, depth first, parents before children."""
    if condition is None:
        return []
    nodes: list[Condition] = [condition]
    for child in _children(condition):
        nodes += walk(child)
    return nodes


def _window_columns(window: RelativeWindow | AbsoluteWindow | TermWindow | None) -> set[str]:
    return {window.column} if window is not None and window.column is not None else set()


def referenced_columns(spec: CohortSpecV1) -> frozenset[str]:
    """Every qualified column the spec names explicitly (terms resolve elsewhere)."""
    columns: set[str] = set()
    for node in walk(spec.where):
        match node:
            case AttributeCondition(filter=value_filter):
                columns.add(value_filter.column)
            case AttributeTimeWindowCondition(window=window):
                columns |= _window_columns(window)
            case RelatedCondition() | MetricCondition():
                for join in node.path or ():
                    columns |= {join.from_column, join.to_column}
                columns |= {f.column for f in node.filters}
                columns |= _window_columns(node.time_window)
                if node.aggregate is not None and node.aggregate.column is not None:
                    columns.add(node.aggregate.column)
    return frozenset(columns)


def referenced_tables(spec: CohortSpecV1) -> frozenset[str]:
    tables = {table_of(c) for c in referenced_columns(spec)}
    tables |= {n.table for n in walk(spec.where) if isinstance(n, RelatedCondition)}
    return frozenset(tables)


def referenced_terms(spec: CohortSpecV1) -> frozenset[str]:
    """Business-context terms (normalized) the spec relies on: metrics and window terms."""
    terms: set[str] = set()
    for node in walk(spec.where):
        if isinstance(node, MetricCondition):
            terms.add(normalize_term(node.term))
        window = getattr(node, "time_window", None) or getattr(node, "window", None)
        if isinstance(window, TermWindow):
            terms.add(normalize_term(window.term))
    return frozenset(terms)


def spec_document(spec: CohortSpecV1) -> dict[str, Any]:
    """The canonical JSON document of a spec (what is stored, hashed and shown)."""
    return spec.model_dump(mode="json", exclude_none=True)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def spec_hash(spec: CohortSpecV1) -> str:
    """SHA-256 of the canonical JSON document (independent of input key order)."""
    return hashlib.sha256(canonical_json(spec_document(spec))).hexdigest()
