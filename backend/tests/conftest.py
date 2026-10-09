"""Shared fixtures."""

import os
import socket
from collections.abc import Iterator

import pytest

from tests.constants import APPDB_PASSWORD, WAREHOUSE_PASSWORD


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Remove every COHORTSPLIT_* variable so tests control the environment fully."""
    for name in list(os.environ):
        if name.startswith("COHORTSPLIT_"):
            monkeypatch.delenv(name)
    yield monkeypatch


@pytest.fixture
def closed_port() -> int:
    """A localhost TCP port with nothing listening on it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture
def unreachable_appdb_env(clean_env: pytest.MonkeyPatch, closed_port: int) -> pytest.MonkeyPatch:
    """Valid settings whose appdb points at a closed port."""
    clean_env.setenv("COHORTSPLIT_APPDB_HOST", "127.0.0.1")
    clean_env.setenv("COHORTSPLIT_APPDB_PORT", str(closed_port))
    clean_env.setenv("COHORTSPLIT_APPDB_NAME", "cohortsplit")
    clean_env.setenv("COHORTSPLIT_APPDB_USER", "cohortsplit")
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", APPDB_PASSWORD)
    clean_env.setenv(
        "COHORTSPLIT_WAREHOUSE_DSN",
        f"postgresql://cohortsplit_ro:{WAREHOUSE_PASSWORD}@127.0.0.1:{closed_port}/ecommerce",
    )
    return clean_env
