"""Injectable time source (tests override ``get_clock``)."""

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


def get_clock() -> Clock:
    """FastAPI dependency returning the clock used for sessions, lockout and audit."""
    return utcnow
