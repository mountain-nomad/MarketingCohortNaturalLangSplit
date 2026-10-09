"""Crawler output: generated schema documentation and data profiles.

These models are what gets persisted as ``semantic_docs`` (origin ``generated``)
and what the content hash is computed over. They contain no timestamps, run ids
or warehouse location, so identical warehouse content yields identical documents.
"""

from pydantic import BaseModel, ConfigDict

from cohortsplit.warehouse.models import TableKind, TypeCategory


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ColumnDoc(_Frozen):
    name: str
    data_type: str
    type_category: TypeCategory
    nullable: bool
    comment: str | None = None
    allowed_values: tuple[str, ...] | None = None


class ForeignKeyDoc(_Frozen):
    name: str
    columns: tuple[str, ...]
    referred_table: str  # qualified "schema.table"
    referred_columns: tuple[str, ...]


class TableDoc(_Frozen):
    """Generated schema document for one table or view."""

    schema_name: str
    name: str
    kind: TableKind
    comment: str | None = None
    columns: tuple[ColumnDoc, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeyDoc, ...]

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"

    def column(self, name: str) -> ColumnDoc | None:
        return next((c for c in self.columns if c.name == name), None)


class ColumnProfile(_Frozen):
    name: str
    sampled: bool
    # Why values were or were not collected (policy decision), for reviewers.
    reason: str
    values: tuple[str, ...] | None = None


class TableProfile(_Frozen):
    """Generated data profile for one table: row count and policy-permitted samples."""

    table: str  # qualified "schema.table"
    estimated_row_count: int | None
    columns: tuple[ColumnProfile, ...]
    # Smallest primary-key values (single-column keys only), used to fill example use cases.
    key_examples: tuple[str, ...] = ()

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)


class WarehouseCatalog(_Frozen):
    dialect: str
    tables: tuple[TableDoc, ...]
    profiles: tuple[TableProfile, ...]

    def table(self, qualified_name: str) -> TableDoc | None:
        return next((t for t in self.tables if t.qualified_name == qualified_name), None)

    def profile(self, qualified_name: str) -> TableProfile | None:
        return next((p for p in self.profiles if p.table == qualified_name), None)

    def sample_values(self, qualified_table: str, column: str) -> tuple[str, ...]:
        profile = self.profile(qualified_table)
        col = profile.column(column) if profile else None
        return col.values or () if col else ()

    def qualified_columns(self) -> frozenset[str]:
        return frozenset(f"{t.qualified_name}.{c.name}" for t in self.tables for c in t.columns)

    def qualified_tables(self) -> frozenset[str]:
        return frozenset(t.qualified_name for t in self.tables)
