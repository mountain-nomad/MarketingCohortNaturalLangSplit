"""Sampling policy: which columns may have sample values read (FR-2, AC-27)."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from cohortsplit.warehouse.models import ColumnInfo, TableRef


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


class SamplingPolicy:
    def __init__(
        self,
        *,
        enabled: bool,
        denylist: Iterable[str],
        export_granted_columns: Iterable[str] = (),
        max_distinct: int = 50,
    ) -> None:
        raise NotImplementedError

    @property
    def enabled(self) -> bool:
        raise NotImplementedError

    @property
    def max_distinct(self) -> int:
        raise NotImplementedError

    def decide(self, table: TableRef, column: ColumnInfo) -> SamplingDecision:
        """May distinct values of ``column`` be read? Fails closed."""
        raise NotImplementedError

    def decide_key_example(self, table: TableRef, column: ColumnInfo) -> SamplingDecision:
        """May the smallest key values of ``column`` be read to fill example use cases?"""
        raise NotImplementedError
