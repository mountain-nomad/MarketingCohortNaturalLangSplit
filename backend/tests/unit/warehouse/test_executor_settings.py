"""Executor limits come from settings: 30s timeout and 1,000,000-row cap by default (FR-6)."""

import pytest

from cohortsplit.config import ConfigError, load_settings
from cohortsplit.warehouse import (
    PostgresWarehouseAdapter,
    WarehouseNotConfiguredError,
    create_warehouse_adapter,
)
from tests.constants import APPDB_PASSWORD, WAREHOUSE_PASSWORD


@pytest.fixture
def base_env(clean_env: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", APPDB_PASSWORD)
    return clean_env


def test_defaults_30s_and_1m_rows(base_env: pytest.MonkeyPatch) -> None:
    settings = load_settings().model_dump()

    assert settings.get("warehouse_statement_timeout_seconds") == 30
    assert settings.get("warehouse_row_cap") == 1_000_000
    assert settings.get("warehouse_connect_timeout_seconds") == 5


def test_limits_configurable_from_env(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("COHORTSPLIT_WAREHOUSE_STATEMENT_TIMEOUT_SECONDS", "2.5")
    base_env.setenv("COHORTSPLIT_WAREHOUSE_ROW_CAP", "10")

    settings = load_settings().model_dump()

    assert settings.get("warehouse_statement_timeout_seconds") == 2.5
    assert settings.get("warehouse_row_cap") == 10


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("COHORTSPLIT_WAREHOUSE_STATEMENT_TIMEOUT_SECONDS", "0"),
        ("COHORTSPLIT_WAREHOUSE_STATEMENT_TIMEOUT_SECONDS", "-1"),
        ("COHORTSPLIT_WAREHOUSE_ROW_CAP", "0"),
    ],
)
def test_non_positive_limits_rejected(base_env: pytest.MonkeyPatch, name: str, value: str) -> None:
    base_env.setenv(name, value)

    with pytest.raises(ConfigError, match=name):
        load_settings()


def test_factory_requires_dsn(base_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(WarehouseNotConfiguredError, match="COHORTSPLIT_WAREHOUSE_DSN"):
        create_warehouse_adapter(load_settings())


def test_factory_passes_limits_to_executor(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv(
        "COHORTSPLIT_WAREHOUSE_DSN",
        f"postgresql://cohortsplit_ro:{WAREHOUSE_PASSWORD}@127.0.0.1:5999/ecommerce",
    )
    base_env.setenv("COHORTSPLIT_WAREHOUSE_STATEMENT_TIMEOUT_SECONDS", "7")
    base_env.setenv("COHORTSPLIT_WAREHOUSE_ROW_CAP", "123")

    adapter = create_warehouse_adapter(load_settings())

    assert isinstance(adapter, PostgresWarehouseAdapter)
    assert adapter.executor.statement_timeout_seconds == 7
    assert adapter.executor.row_cap == 123
    assert adapter.describe_location() == "127.0.0.1:5999/ecommerce"


def test_factory_rejects_unparsable_dsn_without_echoing_it(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("COHORTSPLIT_WAREHOUSE_DSN", f"not a dsn {WAREHOUSE_PASSWORD}=")

    with pytest.raises(WarehouseNotConfiguredError) as excinfo:
        create_warehouse_adapter(load_settings())

    assert "COHORTSPLIT_WAREHOUSE_DSN" in str(excinfo.value)
    assert WAREHOUSE_PASSWORD not in str(excinfo.value)
