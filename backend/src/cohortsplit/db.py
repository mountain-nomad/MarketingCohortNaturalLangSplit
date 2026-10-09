"""Application database (appdb) connectivity."""

from sqlalchemy import URL, Engine

from cohortsplit.config import Settings


class AppDatabaseUnavailable(Exception):
    """Raised when the application database cannot be reached."""


def appdb_url(settings: Settings) -> URL:
    raise NotImplementedError


def create_appdb_engine(settings: Settings) -> Engine:
    raise NotImplementedError


def check_appdb(engine: Engine) -> None:
    raise NotImplementedError
