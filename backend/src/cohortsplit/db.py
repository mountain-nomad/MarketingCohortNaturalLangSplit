"""Application database (appdb) connectivity."""

import logging

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from cohortsplit.config import Settings

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 3


class AppDatabaseUnavailableError(Exception):
    """Raised when the application database cannot be reached."""


def appdb_url(settings: Settings) -> URL:
    """Structured URL; the password never passes through string formatting."""
    return URL.create(
        "postgresql+psycopg",
        username=settings.appdb_user,
        password=settings.appdb_password.get_secret_value(),
        host=settings.appdb_host,
        port=settings.appdb_port,
        database=settings.appdb_name,
    )


def describe_location(url: URL) -> str:
    """``host:port/db`` without credentials, for logs and error messages."""
    return f"{url.host}:{url.port}/{url.database}"


def create_appdb_engine(settings: Settings) -> Engine:
    return create_engine(
        appdb_url(settings),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


def check_appdb(engine: Engine) -> None:
    """Run a trivial query; raise AppDatabaseUnavailableError with an actionable message."""
    location = describe_location(engine.url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        reason = str(getattr(exc, "orig", None) or exc).splitlines()[0]
        logger.warning("appdb check failed location=%s reason=%s", location, reason)
        raise AppDatabaseUnavailableError(
            f"Application database is unreachable at {location}. Check that it is running "
            "and that COHORTSPLIT_APPDB_HOST, COHORTSPLIT_APPDB_PORT, COHORTSPLIT_APPDB_NAME, "
            "COHORTSPLIT_APPDB_USER and COHORTSPLIT_APPDB_PASSWORD are correct."
        ) from exc
