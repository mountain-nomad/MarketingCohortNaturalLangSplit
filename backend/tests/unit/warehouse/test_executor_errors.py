"""Executor errors are typed and actionable, and never leak credentials (AC-28)."""

import logging
import traceback

import pytest
from pydantic import SecretStr

from cohortsplit.warehouse import (
    PostgresWarehouseAdapter,
    QueryTimeoutError,
    ReadOnlyExecutor,
    RowCapExceededError,
    WarehouseUnavailableError,
)
from tests.constants import WAREHOUSE_PASSWORD


def _dsn(port: int) -> SecretStr:
    return SecretStr(f"postgresql://cohortsplit_ro:{WAREHOUSE_PASSWORD}@127.0.0.1:{port}/ecommerce")


def test_unreachable_raises_actionable_error(closed_port: int) -> None:
    executor = ReadOnlyExecutor(_dsn(closed_port), connect_timeout_seconds=2)

    with pytest.raises(WarehouseUnavailableError) as excinfo:
        executor.execute("SELECT 1")

    message = str(excinfo.value)
    assert f"127.0.0.1:{closed_port}/ecommerce" in message
    assert "COHORTSPLIT_WAREHOUSE_DSN" in message


def test_unreachable_error_has_no_password_or_dsn(
    closed_port: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    executor = ReadOnlyExecutor(_dsn(closed_port), connect_timeout_seconds=2)

    with pytest.raises(WarehouseUnavailableError) as excinfo:
        executor.execute("SELECT 1")

    rendered = "".join(traceback.format_exception(excinfo.value))
    assert WAREHOUSE_PASSWORD not in rendered
    assert "postgresql://" not in rendered
    assert WAREHOUSE_PASSWORD not in caplog.text


def test_executor_and_adapter_repr_hide_dsn(closed_port: int) -> None:
    executor = ReadOnlyExecutor(_dsn(closed_port))
    adapter = PostgresWarehouseAdapter(executor)

    for text in (repr(executor), str(executor), repr(adapter), str(adapter), repr(vars(executor))):
        assert WAREHOUSE_PASSWORD not in text

    assert adapter.describe_location() == f"127.0.0.1:{closed_port}/ecommerce"


def test_executor_rejects_non_positive_limits(closed_port: int) -> None:
    with pytest.raises(ValueError, match="statement_timeout_seconds"):
        ReadOnlyExecutor(_dsn(closed_port), statement_timeout_seconds=0)
    with pytest.raises(ValueError, match="row_cap"):
        ReadOnlyExecutor(_dsn(closed_port), row_cap=0)


def test_limit_errors_state_their_values() -> None:
    assert "30s" in str(QueryTimeoutError(30))
    assert QueryTimeoutError(1.5).timeout_seconds == 1.5
    assert "1,000,000" in str(RowCapExceededError(1_000_000))
    assert RowCapExceededError(5).row_cap == 5
