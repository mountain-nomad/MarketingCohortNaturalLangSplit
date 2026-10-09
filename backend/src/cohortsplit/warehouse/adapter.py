"""The warehouse adapter boundary.

Domain code (crawler, compiler, export) talks to warehouses only through this
protocol. Adding a warehouse means adding an implementation, not editing callers.
"""

from collections.abc import Mapping
from typing import Protocol

from cohortsplit.warehouse.models import QueryResult, TableMetadata, TableRef


class WarehouseAdapter(Protocol):
    dialect: str

    def describe_location(self) -> str:
        """``host:port/db`` for logs and errors; never contains credentials."""
        ...

    def test_connection(self) -> None:
        """Raise :class:`WarehouseUnavailableError` if the warehouse cannot be queried."""
        ...

    def list_schemas(self) -> list[str]:
        """Non-system schemas the configured role may use, sorted."""
        ...

    def list_tables(self, schema: str) -> list[TableRef]:
        """Tables/views in ``schema`` the configured role may SELECT from, sorted by name."""
        ...

    def describe_table(self, table: TableRef) -> TableMetadata:
        """Columns, primary key, foreign keys and estimated row count."""
        ...

    def get_distinct_values(
        self, table: TableRef, column: str, max_distinct: int
    ) -> list[str] | None:
        """Up to ``max_distinct`` non-null distinct values (most frequent first, then by value).

        Returns ``None`` when the column has more than ``max_distinct`` distinct values.
        Callers must apply the sampling policy before calling.
        """
        ...

    def get_key_examples(self, table: TableRef, column: str, limit: int) -> list[str]:
        """The ``limit`` smallest non-null values of a key column, as text."""
        ...

    def execute_readonly(self, sql: str, params: Mapping[str, object] | None = None) -> QueryResult:
        """Run one statement in a read-only transaction with timeout and row cap."""
        ...
