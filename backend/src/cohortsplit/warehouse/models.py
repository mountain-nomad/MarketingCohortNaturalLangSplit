"""Warehouse-agnostic metadata and result types returned by adapters."""

from dataclasses import dataclass, field
from typing import Literal

TableKind = Literal["table", "partitioned_table", "view", "materialized_view", "foreign_table"]
TypeCategory = Literal["string", "boolean", "enum", "numeric", "datetime", "other"]


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True, order=True)
class TableRef:
    schema: str
    name: str
    kind: TableKind = "table"
    comment: str | None = field(default=None, compare=False)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    type_category: TypeCategory
    nullable: bool
    ordinal: int
    comment: str | None = None
    # Values permitted by a single-column CHECK (col = ANY (ARRAY[...])) constraint:
    # schema metadata, not sampled data.
    allowed_values: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ForeignKey:
    name: str
    columns: tuple[str, ...]
    referred_schema: str
    referred_table: str
    referred_columns: tuple[str, ...]


@dataclass(frozen=True)
class TableMetadata:
    ref: TableRef
    columns: tuple[ColumnInfo, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKey, ...]
    estimated_row_count: int | None
