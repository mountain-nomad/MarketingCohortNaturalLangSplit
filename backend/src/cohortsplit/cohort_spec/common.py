"""Building blocks shared by cohort specs and business-context definitions.

All references are qualified (``schema.table`` / ``schema.table.column``) and literals are
plain values: nothing here is SQL.
"""

import unicodedata
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from cohortsplit.cohort_spec.draft import QualifiedColumn, QualifiedTable

__all__ = [
    "MAX_IN_VALUES",
    "Filter",
    "FrozenModel",
    "Operator",
    "QualifiedColumn",
    "QualifiedTable",
    "Scalar",
    "normalize_term",
    "table_of",
]

MAX_IN_VALUES = 200

Scalar = str | int | float | bool
Operator = Literal["=", "!=", ">", ">=", "<", "<=", "in", "not_in", "is_null", "is_not_null"]
LIST_OPERATORS = frozenset({"in", "not_in"})
NULL_OPERATORS = frozenset({"is_null", "is_not_null"})


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def normalize_term(value: str) -> str:
    """Trimmed, whitespace-collapsed, case-folded (NFKC) form of a business term."""
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def table_of(column: str) -> str:
    """``schema.table`` of a qualified ``schema.table.column``."""
    return column.rsplit(".", 1)[0]


class Filter(FrozenModel):
    """``column <operator> value`` with a literal value (never SQL)."""

    column: QualifiedColumn
    operator: Operator
    value: Scalar | tuple[Scalar, ...] | None = None

    @model_validator(mode="after")
    def _value_matches_operator(self) -> Self:
        if self.operator in NULL_OPERATORS:
            if self.value is not None:
                raise ValueError(f"operator {self.operator} takes no value")
        elif self.operator in LIST_OPERATORS:
            if not isinstance(self.value, tuple) or not self.value:
                raise ValueError(f"operator {self.operator} needs a non-empty list of values")
            if len(self.value) > MAX_IN_VALUES:
                raise ValueError(f"at most {MAX_IN_VALUES} values")
        elif self.value is None or isinstance(self.value, tuple):
            raise ValueError(f"operator {self.operator} needs a single value")
        return self
