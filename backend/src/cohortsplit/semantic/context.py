"""Business-context entries (FR-3, ruling S1 in docs/decisions/0002-semantic-context-rulings.md).

Human-authored, typed and validated definitions that the cohort compiler consumes
deterministically. Every schema reference is qualified (``schema.table`` /
``schema.table.column``) and literals are plain values: there is no SQL anywhere.

An entry is ``{key, synonyms, description, definition}``; ``definition.kind`` selects the
shape:

* ``term`` — a business word naming a table or a column ("customer" -> public.users);
* ``metric`` — rows of one table matching filters ("purchase" = orders with status in
  paid/shipped/delivered), optionally with a value column (sums, e.g. "spent") and a
  time column (windows);
* ``status_semantics`` — what each value of a status column means;
* ``time_window`` — a default window ("recently" = last 30 days), optionally on a column;
* ``exclusion`` — rows of a table that remove users from every cohort (soft-deleted users);
* ``canonical_user_id`` — the cohort entity key (BR-1); at most one entry.

Keys and synonyms share one namespace (enforced by the service). Reference existence is
checked against the latest crawl by :mod:`cohortsplit.semantic.inventory`.
"""

import re
import unicodedata
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from cohortsplit.cohort_spec.draft import QualifiedColumn, QualifiedTable

ENTRY_KINDS = (
    "term",
    "metric",
    "status_semantics",
    "time_window",
    "exclusion",
    "canonical_user_id",
)
KEY_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
MAX_SYNONYMS = 50
MAX_SYNONYM_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 4000
MAX_FILTERS = 20
MAX_IN_VALUES = 200
MAX_MEANINGS = 100

Scalar = str | int | float | bool
Operator = Literal["=", "!=", ">", ">=", "<", "<=", "in", "not_in", "is_null", "is_not_null"]
_LIST_OPERATORS = frozenset({"in", "not_in"})
_NULL_OPERATORS = frozenset({"is_null", "is_not_null"})


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def table_of(column: str) -> str:
    """``schema.table`` of a qualified ``schema.table.column``."""
    return column.rsplit(".", 1)[0]


class Filter(_Model):
    """``column <operator> value`` with a literal value (never SQL)."""

    column: QualifiedColumn
    operator: Operator
    value: Scalar | tuple[Scalar, ...] | None = None

    @model_validator(mode="after")
    def _value_matches_operator(self) -> Self:
        if self.operator in _NULL_OPERATORS:
            if self.value is not None:
                raise ValueError(f"operator {self.operator} takes no value")
        elif self.operator in _LIST_OPERATORS:
            if not isinstance(self.value, tuple) or not self.value:
                raise ValueError(f"operator {self.operator} needs a non-empty list of values")
            if len(self.value) > MAX_IN_VALUES:
                raise ValueError(f"at most {MAX_IN_VALUES} values")
        elif self.value is None or isinstance(self.value, tuple):
            raise ValueError(f"operator {self.operator} needs a single value")
        return self


def _filters_on(table: str, filters: tuple[Filter, ...], *columns: str | None) -> None:
    outside = sorted(
        {f.column for f in filters if table_of(f.column) != table}
        | {c for c in columns if c is not None and table_of(c) != table}
    )
    if outside:
        raise ValueError(f"columns must belong to {table}: {', '.join(outside)}")


class TermDefinition(_Model):
    kind: Literal["term"] = "term"
    table: QualifiedTable | None = None
    column: QualifiedColumn | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> Self:
        if (self.table is None) == (self.column is None):
            raise ValueError("a term names exactly one table or one column")
        return self


class MetricDefinition(_Model):
    kind: Literal["metric"] = "metric"
    table: QualifiedTable
    filters: tuple[Filter, ...] = Field(default=(), max_length=MAX_FILTERS)
    value_column: QualifiedColumn | None = None
    time_column: QualifiedColumn | None = None

    @model_validator(mode="after")
    def _columns_on_table(self) -> Self:
        _filters_on(self.table, self.filters, self.value_column, self.time_column)
        return self


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class StatusSemanticsDefinition(_Model):
    kind: Literal["status_semantics"] = "status_semantics"
    column: QualifiedColumn
    meanings: dict[NonBlank, NonBlank] = Field(min_length=1, max_length=MAX_MEANINGS)


class TimeWindowDefinition(_Model):
    kind: Literal["time_window"] = "time_window"
    last_days: int = Field(ge=1, le=3650)
    column: QualifiedColumn | None = None


class ExclusionDefinition(_Model):
    kind: Literal["exclusion"] = "exclusion"
    table: QualifiedTable
    filters: tuple[Filter, ...] = Field(min_length=1, max_length=MAX_FILTERS)

    @model_validator(mode="after")
    def _columns_on_table(self) -> Self:
        _filters_on(self.table, self.filters)
        return self


class CanonicalUserIdDefinition(_Model):
    kind: Literal["canonical_user_id"] = "canonical_user_id"
    column: QualifiedColumn


Definition = Annotated[
    TermDefinition
    | MetricDefinition
    | StatusSemanticsDefinition
    | TimeWindowDefinition
    | ExclusionDefinition
    | CanonicalUserIdDefinition,
    Field(discriminator="kind"),
]


def normalize_term(value: str) -> str:
    """Trimmed, whitespace-collapsed, case-folded (NFKC) form of a business term."""
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _normalize_synonyms(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for raw in values:
        term = normalize_term(raw)
        if not term:
            raise ValueError("synonyms must not be blank")
        if len(term) > MAX_SYNONYM_LENGTH:
            raise ValueError(f"synonyms are at most {MAX_SYNONYM_LENGTH} characters")
        normalized.add(term)
    return tuple(sorted(normalized))


Key = Annotated[str, StringConstraints(pattern=KEY_PATTERN)]
Synonyms = Annotated[
    tuple[str, ...], Field(max_length=MAX_SYNONYMS), AfterValidator(_normalize_synonyms)
]


class BusinessContextIn(_Model):
    """A business-context entry as submitted by an editor (API body and service input)."""

    key: Key
    synonyms: Synonyms = ()
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    definition: Definition

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str) -> str:
        return value.strip()

    def terms(self) -> frozenset[str]:
        """The key (underscores read as spaces) and synonyms, normalized."""
        return frozenset({normalize_term(self.key.replace("_", " ")), *self.synonyms})


def definition_references(definition: Definition) -> tuple[frozenset[str], frozenset[str]]:
    """``(tables, columns)`` the definition refers to; a column implies its table."""
    tables: set[str] = set()
    columns: set[str] = set()
    match definition:
        case TermDefinition(table=table, column=column):
            if table is not None:
                tables.add(table)
            if column is not None:
                columns.add(column)
        case MetricDefinition():
            tables.add(definition.table)
            columns |= {f.column for f in definition.filters}
            columns |= {
                c for c in (definition.value_column, definition.time_column) if c is not None
            }
        case StatusSemanticsDefinition(column=column) | CanonicalUserIdDefinition(column=column):
            columns.add(column)
        case TimeWindowDefinition(column=column):
            if column is not None:
                columns.add(column)
        case ExclusionDefinition():
            tables.add(definition.table)
            columns |= {f.column for f in definition.filters}
    tables |= {table_of(c) for c in columns}
    return frozenset(tables), frozenset(columns)


_KEY_RE = re.compile(KEY_PATTERN)


def is_valid_key(key: str) -> bool:
    return bool(_KEY_RE.fullmatch(key))
