"""Read-only executor against the demo warehouse (AC-12, AC-13, FR-6 row cap)."""

import time

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr

from cohortsplit.warehouse import (
    QueryTimeoutError,
    ReadOnlyExecutor,
    ReadOnlyViolationError,
    RowCapExceededError,
    WarehousePermissionError,
    WarehouseQueryError,
    WarehouseUnavailableError,
)

pytestmark = pytest.mark.integration

WRITE_STATEMENTS = {
    "insert": (
        "INSERT INTO users (email, password_hash, first_name, last_name) "
        "VALUES ('probe@example.com', 'x', 'probe', 'probe')"
    ),
    "update": "UPDATE users SET first_name = 'probe'",
    "delete": "DELETE FROM order_status_history",
    "create_table": "CREATE TABLE public.cohortsplit_probe (id int)",
    "create_temp_table": "CREATE TEMP TABLE cohortsplit_probe_tmp (id int)",
    "drop_table": "DROP TABLE users",
    "truncate": "TRUNCATE order_status_history",
    "alter_table": "ALTER TABLE users ADD COLUMN cohortsplit_probe int",
    "grant": "GRANT SELECT ON users TO PUBLIC",
}


@pytest.fixture
def executor(warehouse_ro_dsn: str) -> ReadOnlyExecutor:
    return ReadOnlyExecutor(SecretStr(warehouse_ro_dsn), statement_timeout_seconds=5, row_cap=100)


def _fingerprint(dsn: str) -> tuple[int, int, str]:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM order_status_history), "
            "(SELECT string_agg(first_name, ',' ORDER BY user_id) FROM users)"
        ).fetchone()
    assert row is not None
    return (int(row[0]), int(row[1]), str(row[2]))


def test_executor_connects_as_read_only_role(executor: ReadOnlyExecutor) -> None:
    result = executor.execute(
        "SELECT current_user AS who, current_setting('transaction_read_only')"
    )

    assert result.columns[0] == "who"
    assert result.rows == (("cohortsplit_ro", "on"),)


def test_select_with_params(executor: ReadOnlyExecutor) -> None:
    result = executor.execute("SELECT %(n)s::int + 1 AS v", {"n": 41})

    assert result.rows == ((42,),)


@pytest.mark.parametrize("statement", WRITE_STATEMENTS.values(), ids=WRITE_STATEMENTS.keys())
def test_executor_write_refused_by_database(
    executor: ReadOnlyExecutor, warehouse_ro_dsn: str, statement: str
) -> None:
    before = _fingerprint(warehouse_ro_dsn)

    with pytest.raises((ReadOnlyViolationError, WarehousePermissionError)):
        executor.execute(statement)

    assert _fingerprint(warehouse_ro_dsn) == before


def test_multiple_statements_refused(executor: ReadOnlyExecutor, warehouse_ro_dsn: str) -> None:
    before = _fingerprint(warehouse_ro_dsn)

    with pytest.raises((WarehouseQueryError, ReadOnlyViolationError, WarehousePermissionError)):
        executor.execute("COMMIT; UPDATE users SET first_name = 'probe'")

    assert _fingerprint(warehouse_ro_dsn) == before


def test_cannot_switch_transaction_to_read_write(executor: ReadOnlyExecutor) -> None:
    with pytest.raises((WarehouseQueryError, ReadOnlyViolationError)):
        executor.execute("SET TRANSACTION READ WRITE")


def test_statement_timeout_aborts_query_with_timeout_error(warehouse_ro_dsn: str) -> None:
    executor = ReadOnlyExecutor(SecretStr(warehouse_ro_dsn), statement_timeout_seconds=1)

    started = time.monotonic()
    with pytest.raises(QueryTimeoutError) as excinfo:
        executor.execute("SELECT pg_sleep(10)")
    elapsed = time.monotonic() - started

    assert elapsed < 5
    assert excinfo.value.timeout_seconds == 1


def test_timeout_error_states_value(warehouse_ro_dsn: str) -> None:
    executor = ReadOnlyExecutor(SecretStr(warehouse_ro_dsn), statement_timeout_seconds=0.5)

    with pytest.raises(QueryTimeoutError, match=r"0\.5s"):
        executor.execute("SELECT pg_sleep(5)")


def test_timeout_does_not_leak_into_next_query(warehouse_ro_dsn: str) -> None:
    executor = ReadOnlyExecutor(SecretStr(warehouse_ro_dsn), statement_timeout_seconds=1)
    with pytest.raises(QueryTimeoutError):
        executor.execute("SELECT pg_sleep(5)")

    assert executor.execute("SELECT 1").rows == ((1,),)


def test_row_cap_exceeded_raises_with_cap(executor: ReadOnlyExecutor) -> None:
    with pytest.raises(RowCapExceededError) as excinfo:
        executor.execute("SELECT generate_series(1, 101)")

    assert excinfo.value.row_cap == 100


def test_rows_at_cap_are_returned(executor: ReadOnlyExecutor) -> None:
    result = executor.execute("SELECT generate_series(1, 100) AS n")

    assert len(result.rows) == 100
    assert result.rows[0] == (1,)


def test_per_call_row_cap_override(executor: ReadOnlyExecutor) -> None:
    with pytest.raises(RowCapExceededError) as excinfo:
        executor.execute("SELECT generate_series(1, 3)", row_cap=2)

    assert excinfo.value.row_cap == 2


def test_query_error_is_typed(executor: ReadOnlyExecutor) -> None:
    with pytest.raises(WarehouseQueryError, match="cohortsplit_no_such_table"):
        executor.execute("SELECT * FROM cohortsplit_no_such_table")


def test_wrong_password_is_unavailable_without_password(warehouse_ro_dsn: str) -> None:
    wrong = "wrong-pw-91c2aa"
    params = conninfo_to_dict(warehouse_ro_dsn)
    params["password"] = wrong
    executor = ReadOnlyExecutor(SecretStr(make_conninfo(**params)))  # type: ignore[arg-type]

    with pytest.raises(WarehouseUnavailableError) as excinfo:
        executor.execute("SELECT 1")

    assert wrong not in str(excinfo.value)
    assert excinfo.value.__cause__ is None or wrong not in str(excinfo.value.__cause__)
