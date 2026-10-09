"""Alembic environment for the application database (appdb).

The connection URL is built from COHORTSPLIT_APPDB_* settings as a structured
SQLAlchemy URL; it is never written to alembic.ini or rendered into logs.
"""

from logging.config import fileConfig

from alembic import context

from cohortsplit.config import load_settings
from cohortsplit.db import create_appdb_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# No ORM metadata yet; later features register their models here.
target_metadata = None


def run_migrations_offline() -> None:
    raise RuntimeError("Offline (--sql) migrations are not supported; run against appdb.")


def run_migrations_online() -> None:
    engine = create_appdb_engine(load_settings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
