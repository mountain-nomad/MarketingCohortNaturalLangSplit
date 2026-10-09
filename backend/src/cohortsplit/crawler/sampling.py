"""Sampling policy: which columns may have values read (FR-2, AC-27).

Fails closed. A column is sampled only when sampling is enabled, its type is
categorical (string, boolean, enum), it is not export-granted, and it matches no
denylist pattern. The built-in default denylist always applies, whatever
patterns the caller passes.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Protocol

from cohortsplit.warehouse.models import ColumnInfo, TableRef, TypeCategory

# Column-name globs (case-insensitive) that are never sampled. Configured patterns
# EXTEND this list; the built-in defaults cannot be removed by configuration.
DEFAULT_SAMPLE_DENYLIST: tuple[str, ...] = (
    "email",
    "*email*",
    "phone",
    "*phone*",
    "password*",
    "*password*",
    "*token*",
    "address*",
    "*secret*",
    "*_hash",
    "first_name",
    "last_name",
    "*line1",
    "*line2",
    "*postal_code*",
    "ship_name",
    "full_name",
    "*username*",
    "*user_name*",
    "*ssn*",
    "*birth*",
    "dob",
    "*_dob",
    "*ip_address*",
    "*passport*",
    "*iban*",
    "*card_number*",
    "*card_no*",
    "*tax_id*",
)

SAMPLEABLE_CATEGORIES: frozenset[TypeCategory] = frozenset({"string", "boolean", "enum"})
# Key examples: integer surrogate keys only (never codes, UUIDs or other string keys).
KEY_EXAMPLE_TYPES: frozenset[str] = frozenset({"smallint", "integer", "bigint"})


@dataclass(frozen=True)
class SamplingDecision:
    allowed: bool
    reason: str


class ExportGrantProvider(Protocol):
    """Columns granted for export to any role ("schema.table.column" or "table.column").

    Export-granted columns are never sampled. The authentication/RBAC branch provides
    the real implementation; until then :class:`NoExportGrants` is used.
    """

    def export_granted_columns(self) -> frozenset[str]: ...


class NoExportGrants:
    def export_granted_columns(self) -> frozenset[str]:
        return frozenset()


class StaticExportGrants:
    def __init__(self, columns: Iterable[str]) -> None:
        self._columns = frozenset(columns)

    def export_granted_columns(self) -> frozenset[str]:
        return self._columns


def _names(table: TableRef, column: str) -> tuple[str, str, str]:
    """Lower-cased column, ``table.column`` and ``schema.table.column``."""
    col = column.lower()
    return col, f"{table.name.lower()}.{col}", f"{table.schema.lower()}.{table.name.lower()}.{col}"


class SamplingPolicy:
    def __init__(
        self,
        *,
        enabled: bool,
        denylist: Iterable[str],
        export_granted_columns: Iterable[str] = (),
        max_distinct: int = 50,
    ) -> None:
        if max_distinct < 1:
            raise ValueError("max_distinct must be at least 1")
        self._enabled = enabled
        self._max_distinct = max_distinct
        patterns = (*DEFAULT_SAMPLE_DENYLIST, *denylist)
        self._denylist = tuple(dict.fromkeys(p.strip().lower() for p in patterns if p.strip()))
        self._export_granted = frozenset(c.strip().lower() for c in export_granted_columns)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def max_distinct(self) -> int:
        return self._max_distinct

    def _common(self, table: TableRef, column: ColumnInfo) -> SamplingDecision | None:
        if not self._enabled:
            return SamplingDecision(False, "sampling disabled by configuration")
        col, table_col, full = _names(table, column.name)
        if table_col in self._export_granted or full in self._export_granted:
            return SamplingDecision(False, "export-granted column: never sampled")
        for pattern in self._denylist:
            # Unqualified patterns match the column name; dotted ones the qualified name.
            dots = pattern.count(".")
            target = col if dots == 0 else table_col if dots == 1 else full
            if fnmatchcase(target, pattern):
                return SamplingDecision(False, f"matches sampling denylist pattern {pattern!r}")
        return None

    def decide(self, table: TableRef, column: ColumnInfo) -> SamplingDecision:
        """May distinct values of ``column`` be read?"""
        denied = self._common(table, column)
        if denied is not None:
            return denied
        if column.type_category not in SAMPLEABLE_CATEGORIES:
            return SamplingDecision(False, f"type not sampled ({column.type_category})")
        return SamplingDecision(True, "low-cardinality candidate")

    def decide_key_example(self, table: TableRef, column: ColumnInfo) -> SamplingDecision:
        """May the smallest key values of ``column`` be read to fill example use cases?"""
        denied = self._common(table, column)
        if denied is not None:
            return denied
        if column.type_category != "numeric" or column.data_type not in KEY_EXAMPLE_TYPES:
            return SamplingDecision(False, f"not an integer key ({column.data_type})")
        return SamplingDecision(True, "key example")
