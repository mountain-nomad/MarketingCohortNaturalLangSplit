"""PostgreSQL implementation of :class:`WarehouseAdapter`."""

from collections.abc import Mapping

from cohortsplit.warehouse.executor import ReadOnlyExecutor
from cohortsplit.warehouse.models import QueryResult, TableMetadata, TableRef


class PostgresWarehouseAdapter:
    dialect = "postgresql"

    def __init__(self, executor: ReadOnlyExecutor) -> None:
        raise NotImplementedError

    @property
    def executor(self) -> ReadOnlyExecutor:
        raise NotImplementedError

    def describe_location(self) -> str:
        raise NotImplementedError

    def test_connection(self) -> None:
        raise NotImplementedError

    def list_schemas(self) -> list[str]:
        raise NotImplementedError

    def list_tables(self, schema: str) -> list[TableRef]:
        raise NotImplementedError

    def describe_table(self, table: TableRef) -> TableMetadata:
        raise NotImplementedError

    def get_distinct_values(
        self, table: TableRef, column: str, max_distinct: int
    ) -> list[str] | None:
        raise NotImplementedError

    def get_key_examples(self, table: TableRef, column: str, limit: int) -> list[str]:
        raise NotImplementedError

    def execute_readonly(self, sql: str, params: Mapping[str, object] | None = None) -> QueryResult:
        raise NotImplementedError
