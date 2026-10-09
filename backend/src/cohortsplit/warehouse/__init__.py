"""Warehouse adapter boundary: introspection and read-only execution."""

from cohortsplit.warehouse.adapter import WarehouseAdapter
from cohortsplit.warehouse.errors import (
    QueryTimeoutError,
    ReadOnlyViolationError,
    RowCapExceededError,
    WarehouseError,
    WarehouseNotConfiguredError,
    WarehousePermissionError,
    WarehouseQueryError,
    WarehouseUnavailableError,
)
from cohortsplit.warehouse.executor import ReadOnlyExecutor
from cohortsplit.warehouse.factory import create_warehouse_adapter
from cohortsplit.warehouse.models import (
    ColumnInfo,
    ForeignKey,
    QueryResult,
    TableMetadata,
    TableRef,
)
from cohortsplit.warehouse.postgres import PostgresWarehouseAdapter

__all__ = [
    "ColumnInfo",
    "ForeignKey",
    "PostgresWarehouseAdapter",
    "QueryResult",
    "QueryTimeoutError",
    "ReadOnlyExecutor",
    "ReadOnlyViolationError",
    "RowCapExceededError",
    "TableMetadata",
    "TableRef",
    "WarehouseAdapter",
    "WarehouseError",
    "WarehouseNotConfiguredError",
    "WarehousePermissionError",
    "WarehouseQueryError",
    "WarehouseUnavailableError",
    "create_warehouse_adapter",
]
