"""Typed, actionable warehouse errors.

Messages name the warehouse location (``host:port/db``) and the setting to check,
never the DSN or any credential.
"""


class WarehouseError(Exception):
    """Base class for every warehouse failure."""


class WarehouseNotConfiguredError(WarehouseError):
    """``COHORTSPLIT_WAREHOUSE_DSN`` is missing or unparsable."""


class WarehouseUnavailableError(WarehouseError):
    """The warehouse could not be reached or refused the login."""


class QueryTimeoutError(WarehouseError):
    """The statement exceeded the configured statement timeout."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Warehouse query exceeded the statement timeout of {timeout_seconds:g}s and was "
            "aborted. Narrow the request or raise COHORTSPLIT_WAREHOUSE_STATEMENT_TIMEOUT_SECONDS."
        )


class RowCapExceededError(WarehouseError):
    """The result has more rows than the configured row cap."""

    def __init__(self, row_cap: int) -> None:
        self.row_cap = row_cap
        super().__init__(
            f"Warehouse query returned more than the row cap of {row_cap:,} rows; the result "
            "was discarded. Narrow the request or raise COHORTSPLIT_WAREHOUSE_ROW_CAP."
        )


class ReadOnlyViolationError(WarehouseError):
    """The database refused a write because the transaction is read-only."""


class WarehousePermissionError(WarehouseError):
    """The database refused the statement for lack of privileges."""


class WarehouseQueryError(WarehouseError):
    """Any other database error (message sanitized, no credentials)."""
