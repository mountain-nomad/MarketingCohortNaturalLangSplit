"""Read-only statement execution against the warehouse.

Every statement runs in its own connection and its own ``READ ONLY`` transaction
with a transaction-local ``statement_timeout``. The transaction is always rolled
back. Results are streamed and the row cap is enforced while reading, so an
oversized result is never materialised. The DSN is held privately and never
appears in ``repr``, error messages or logs (AC-28): connection failures are
re-raised ``from None`` with a message that names only ``host:port/db``.
"""

import logging
from collections.abc import Mapping

import psycopg
from psycopg import errors as pg_errors
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

from cohortsplit.warehouse.errors import (
    QueryTimeoutError,
    ReadOnlyViolationError,
    RowCapExceededError,
    WarehouseNotConfiguredError,
    WarehousePermissionError,
    WarehouseQueryError,
    WarehouseUnavailableError,
)
from cohortsplit.warehouse.models import QueryResult

logger = logging.getLogger(__name__)

DEFAULT_STATEMENT_TIMEOUT_SECONDS = 30.0
DEFAULT_ROW_CAP = 1_000_000
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5

DSN_SETTING = "COHORTSPLIT_WAREHOUSE_DSN"


def describe_dsn_location(dsn: SecretStr) -> str:
    """``host:port/db`` of a libpq DSN/URL; raises if it cannot be parsed. No credentials."""
    try:
        params = conninfo_to_dict(dsn.get_secret_value())
    except psycopg.ProgrammingError:
        # The parser's message may quote fragments of the DSN: never chain it.
        raise WarehouseNotConfiguredError(
            f"{DSN_SETTING} is not a valid PostgreSQL connection string "
            "(expected postgresql://user:password@host:port/dbname, password URL-encoded)."
        ) from None
    host = str(params.get("host") or params.get("hostaddr") or "localhost")
    port = str(params.get("port") or "5432")
    dbname = str(params.get("dbname") or params.get("user") or "")
    return f"{host}:{port}/{dbname}"


class ReadOnlyExecutor:
    def __init__(
        self,
        dsn: SecretStr,
        *,
        statement_timeout_seconds: float = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
        row_cap: int = DEFAULT_ROW_CAP,
        connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        if statement_timeout_seconds <= 0:
            raise ValueError("statement_timeout_seconds must be positive")
        if row_cap < 1:
            raise ValueError("row_cap must be at least 1")
        if connect_timeout_seconds < 1:
            raise ValueError("connect_timeout_seconds must be at least 1")
        self.__dsn = dsn
        self._location = describe_dsn_location(dsn)
        self._statement_timeout_seconds = float(statement_timeout_seconds)
        self._row_cap = row_cap
        self._connect_timeout_seconds = connect_timeout_seconds

    def __repr__(self) -> str:
        return (
            f"ReadOnlyExecutor(location={self._location!r}, "
            f"statement_timeout_seconds={self._statement_timeout_seconds:g}, "
            f"row_cap={self._row_cap})"
        )

    @property
    def statement_timeout_seconds(self) -> float:
        return self._statement_timeout_seconds

    @property
    def row_cap(self) -> int:
        return self._row_cap

    def describe_location(self) -> str:
        return self._location

    def _connect(self) -> psycopg.Connection[tuple[object, ...]]:
        try:
            conn = psycopg.connect(
                self.__dsn.get_secret_value(),
                connect_timeout=self._connect_timeout_seconds,
                application_name="cohortsplit",
            )
        except psycopg.Error as exc:
            logger.warning(
                "warehouse connect failed location=%s error=%s",
                self._location,
                type(exc).__name__,
            )
            raise WarehouseUnavailableError(
                f"Warehouse is unreachable at {self._location} or refused the login. Check "
                f"that it is running and that {DSN_SETTING} (host, port, database, user, "
                "password) is correct."
            ) from None
        conn.read_only = True
        return conn

    def execute(
        self,
        sql: str,
        params: Mapping[str, object] | None = None,
        *,
        row_cap: int | None = None,
    ) -> QueryResult:
        cap = self._row_cap if row_cap is None else row_cap
        if cap < 1:
            raise ValueError("row_cap must be at least 1")
        timeout_ms = max(1, round(self._statement_timeout_seconds * 1000))
        with self._connect() as conn:
            try:
                with conn.transaction(force_rollback=True), conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('statement_timeout', %(ms)s, true)",
                        {"ms": str(timeout_ms)},
                    )
                    rows: list[tuple[object, ...]] = []
                    # stream() always uses the extended protocol: one statement only.
                    for row in cur.stream(sql, None if params is None else dict(params)):
                        rows.append(row)
                        if len(rows) > cap:
                            raise RowCapExceededError(cap)
                    columns = tuple(d.name for d in cur.description or ())
            except psycopg.Error as exc:
                raise self._translate(exc) from None
        return QueryResult(columns=columns, rows=tuple(rows))

    def _translate(self, exc: psycopg.Error) -> Exception:
        sqlstate = exc.sqlstate or "-"
        primary = (exc.diag.message_primary if exc.diag else None) or type(exc).__name__
        logger.info(
            "warehouse statement failed location=%s sqlstate=%s error=%s",
            self._location,
            sqlstate,
            type(exc).__name__,
        )
        if isinstance(exc, pg_errors.QueryCanceled):
            return QueryTimeoutError(self._statement_timeout_seconds)
        if isinstance(exc, pg_errors.ReadOnlySqlTransaction):
            return ReadOnlyViolationError(
                f"The warehouse refused a write in a read-only transaction: {primary}"
            )
        if isinstance(exc, pg_errors.InsufficientPrivilege):
            return WarehousePermissionError(
                f"The warehouse role lacks the privilege for this statement: {primary}"
            )
        if isinstance(exc, psycopg.OperationalError) and exc.sqlstate is None:
            return WarehouseUnavailableError(
                f"Lost the connection to the warehouse at {self._location}. Check that it is "
                f"running and reachable ({DSN_SETTING})."
            )
        return WarehouseQueryError(f"Warehouse query failed (SQLSTATE {sqlstate}): {primary}")
