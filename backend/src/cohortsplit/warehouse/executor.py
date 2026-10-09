"""Read-only statement execution against the warehouse."""

from collections.abc import Mapping

from pydantic import SecretStr

from cohortsplit.warehouse.models import QueryResult

DEFAULT_STATEMENT_TIMEOUT_SECONDS = 30.0
DEFAULT_ROW_CAP = 1_000_000
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5


class ReadOnlyExecutor:
    def __init__(
        self,
        dsn: SecretStr,
        *,
        statement_timeout_seconds: float = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
        row_cap: int = DEFAULT_ROW_CAP,
        connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        raise NotImplementedError

    @property
    def statement_timeout_seconds(self) -> float:
        raise NotImplementedError

    @property
    def row_cap(self) -> int:
        raise NotImplementedError

    def describe_location(self) -> str:
        raise NotImplementedError

    def execute(
        self,
        sql: str,
        params: Mapping[str, object] | None = None,
        *,
        row_cap: int | None = None,
    ) -> QueryResult:
        raise NotImplementedError
