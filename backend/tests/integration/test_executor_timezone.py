"""Review finding 4: time-window comparisons must not depend on the session TimeZone.

Absolute windows are UTC (ruling C1). The executor pins ``TimeZone=UTC`` per transaction,
so a ``date`` value on 2024-01-01 is inside a window starting 2024-01-01T00:00Z even when
the role, database or DSN defaults to another zone.
"""

import pytest
from pydantic import SecretStr

from cohortsplit.warehouse import ReadOnlyExecutor

pytestmark = pytest.mark.integration


def berlin(dsn: str) -> str:
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}options=-c%20TimeZone%3DEurope%2FBerlin"


def test_executor_pins_utc_regardless_of_session_default(warehouse_ro_dsn: str) -> None:
    executor = ReadOnlyExecutor(SecretStr(berlin(warehouse_ro_dsn)))

    [(zone,)] = executor.execute("SELECT current_setting('TimeZone')").rows

    assert zone == "UTC"


def test_date_column_window_bound_is_utc(warehouse_ro_dsn: str) -> None:
    executor = ReadOnlyExecutor(SecretStr(berlin(warehouse_ro_dsn)))

    [(inside,)] = executor.execute(
        "SELECT CAST('2024-01-01' AS date) >= CAST(%(start)s AS TIMESTAMPTZ)",
        {"start": "2024-01-01T00:00:00+00:00"},
    ).rows

    assert inside is True
