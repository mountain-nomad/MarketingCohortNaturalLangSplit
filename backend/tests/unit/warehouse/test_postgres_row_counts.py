"""Row counts never abort introspection and never full-scan partitioned tables."""

from collections.abc import Mapping

import pytest

from cohortsplit.warehouse import (
    PostgresWarehouseAdapter,
    QueryResult,
    QueryTimeoutError,
    TableRef,
    WarehousePermissionError,
)


class FakeExecutor:
    """Answers the adapter's catalog queries; ``count(*)`` raises ``count_error``."""

    def __init__(self, reltuples: float, count_error: Exception | None = None) -> None:
        self.reltuples = reltuples
        self.count_error = count_error
        self.statements: list[str] = []

    def describe_location(self) -> str:
        return "fake:5432/db"

    def execute(
        self, sql: str, params: Mapping[str, object] | None = None, *, row_cap: int | None = None
    ) -> QueryResult:
        self.statements.append(sql)
        if "count(*)" in sql:
            if self.count_error is not None:
                raise self.count_error
            return QueryResult(("count",), ((7,),))
        if "pg_partition_tree" in sql:
            return QueryResult(("rows", "known"), ((1234.0, True),))
        if "reltuples" in sql:
            return QueryResult(("oid", "reltuples"), ((42, self.reltuples),))
        if "pg_constraint" in sql:
            return QueryResult((), ())
        if "pg_attribute" in sql:
            return QueryResult((), (("id", "bigint", "N", False, 1, None),))
        raise AssertionError(f"unexpected statement: {sql}")


def _adapter(executor: FakeExecutor) -> PostgresWarehouseAdapter:
    return PostgresWarehouseAdapter(executor)  # type: ignore[arg-type]


@pytest.mark.parametrize("error", [QueryTimeoutError(30), WarehousePermissionError("denied")])
def test_failed_exact_count_records_unknown_instead_of_failing(error: Exception) -> None:
    executor = FakeExecutor(reltuples=-1, count_error=error)

    meta = _adapter(executor).describe_table(TableRef("public", "big"))

    assert meta.estimated_row_count is None
    assert [c.name for c in meta.columns] == ["id"]


def test_never_analyzed_table_gets_exact_count() -> None:
    meta = _adapter(FakeExecutor(reltuples=-1)).describe_table(TableRef("public", "small"))

    assert meta.estimated_row_count == 7


def test_partitioned_table_sums_partitions_without_count() -> None:
    executor = FakeExecutor(reltuples=-1)

    meta = _adapter(executor).describe_table(TableRef("public", "events", kind="partitioned_table"))

    assert meta.estimated_row_count == 1234
    assert not [s for s in executor.statements if "count(*)" in s]
