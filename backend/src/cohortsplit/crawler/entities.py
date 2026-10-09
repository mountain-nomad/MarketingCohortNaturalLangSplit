"""Detect the user entity table from schema metadata."""

from collections.abc import Sequence

from cohortsplit.crawler.catalog import TableDoc

USER_TABLE_NAMES: tuple[str, ...] = (
    "users",
    "user",
    "customers",
    "customer",
    "accounts",
    "members",
)


def detect_user_table(tables: Sequence[TableDoc], configured: str | None = None) -> TableDoc | None:
    """The configured table ("schema.table"), else the first table named like a user entity
    with a single-column primary key (schemas in sorted order). ``None`` if there is none."""
    raise NotImplementedError
