"""Draft cohort specification, version ``draft-0``.

A deliberately minimal, versioned placeholder so the crawler can store example
use cases (NL request -> structured spec) before the real cohort specification
exists. The compiler branch owns the real spec and must migrate stored
``draft-0`` documents (see docs/decisions/0001-draft-cohort-spec.md).

All table references are qualified ``schema.table`` and all column references
``schema.table.column``. Values are literals; nothing here is executable SQL.
"""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SPEC_VERSION: Literal["draft-0"] = "draft-0"

_TABLE = re.compile(r"^[^.\s]+\.[^.\s]+$")
_COLUMN = re.compile(r"^[^.\s]+\.[^.\s]+\.[^.\s]+$")

Scalar = str | int | float | bool


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_column(value: str) -> str:
    if not _COLUMN.match(value):
        raise ValueError('must be a qualified column "schema.table.column"')
    return value


def _check_table(value: str) -> str:
    if not _TABLE.match(value):
        raise ValueError('must be a qualified table "schema.table"')
    return value


QualifiedColumn = Annotated[str, AfterValidator(_check_column)]
QualifiedTable = Annotated[str, AfterValidator(_check_table)]


class EntityRef(_Model):
    table: QualifiedTable
    key: str = Field(min_length=1)


class Join(_Model):
    """Equality join between two qualified columns, walked from the entity outwards."""

    from_column: QualifiedColumn
    to_column: QualifiedColumn


class ValueFilter(_Model):
    column: QualifiedColumn
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "in"]
    value: Scalar | tuple[Scalar, ...]


class TimeWindow(_Model):
    column: QualifiedColumn
    last_days: int = Field(ge=1)


class Aggregate(_Model):
    """``count`` of related rows, or ``sum`` of a related column, compared to a threshold."""

    function: Literal["count", "sum"]
    column: QualifiedColumn | None = None
    operator: Literal[">", ">=", "=", "<", "<="]
    value: int | float


class AttributeCondition(_Model):
    """A filter on a column of the entity table itself."""

    type: Literal["attribute"] = "attribute"
    filter: ValueFilter


class AttributeTimeWindowCondition(_Model):
    """An entity timestamp column within the last N days."""

    type: Literal["attribute_time_window"] = "attribute_time_window"
    window: TimeWindow


class RelatedCondition(_Model):
    """Existence (or a count/sum threshold) of related rows reached through ``path``."""

    type: Literal["related"] = "related"
    path: tuple[Join, ...] = Field(min_length=1)
    filters: tuple[ValueFilter, ...] = ()
    time_window: TimeWindow | None = None
    aggregate: Aggregate | None = None


class AllOf(_Model):
    type: Literal["all_of"] = "all_of"
    conditions: tuple["Condition", ...] = Field(min_length=1)


class AnyOf(_Model):
    type: Literal["any_of"] = "any_of"
    conditions: tuple["Condition", ...] = Field(min_length=1)


class Not(_Model):
    type: Literal["not"] = "not"
    condition: "Condition"


Condition = Annotated[
    AttributeCondition | AttributeTimeWindowCondition | RelatedCondition | AllOf | AnyOf | Not,
    Field(discriminator="type"),
]


class DraftCohortSpec(_Model):
    spec_version: Literal["draft-0"] = SPEC_VERSION
    entity: EntityRef
    where: Condition


AllOf.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()
DraftCohortSpec.model_rebuild()


def referenced_columns(spec: DraftCohortSpec) -> frozenset[str]:
    """Every qualified column the spec depends on, including the entity key."""
    raise NotImplementedError


def referenced_tables(spec: DraftCohortSpec) -> frozenset[str]:
    """Every qualified table the spec depends on."""
    raise NotImplementedError
