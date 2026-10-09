"""Build the configured warehouse adapter from settings."""

from cohortsplit.config import Settings
from cohortsplit.warehouse.postgres import PostgresWarehouseAdapter


def create_warehouse_adapter(settings: Settings) -> PostgresWarehouseAdapter:
    raise NotImplementedError
