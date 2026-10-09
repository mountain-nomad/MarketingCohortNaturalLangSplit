"""Build the configured warehouse adapter from settings."""

from cohortsplit.config import Settings
from cohortsplit.warehouse.errors import WarehouseNotConfiguredError
from cohortsplit.warehouse.executor import DSN_SETTING, ReadOnlyExecutor
from cohortsplit.warehouse.postgres import PostgresWarehouseAdapter


def create_warehouse_adapter(settings: Settings) -> PostgresWarehouseAdapter:
    """PostgreSQL is the only MVP dialect; other warehouses add an adapter here."""
    if settings.warehouse_dsn is None:
        raise WarehouseNotConfiguredError(
            f"No warehouse is configured. Set {DSN_SETTING} to a read-only "
            "postgresql://user:password@host:port/dbname connection string."
        )
    executor = ReadOnlyExecutor(
        settings.warehouse_dsn,
        statement_timeout_seconds=settings.warehouse_statement_timeout_seconds,
        row_cap=settings.warehouse_row_cap,
        connect_timeout_seconds=settings.warehouse_connect_timeout_seconds,
    )
    return PostgresWarehouseAdapter(executor)
