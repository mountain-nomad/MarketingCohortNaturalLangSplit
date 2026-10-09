"""Detect the user entity table from schema metadata."""

from collections.abc import Sequence

from cohortsplit.crawler.catalog import TableDoc

# Template vocabulary, not customer schema: names commonly used for the user entity.
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
    if configured is not None:
        return next((t for t in tables if t.qualified_name == configured), None)
    candidates = [
        t
        for t in tables
        if t.name.lower() in USER_TABLE_NAMES and len(t.primary_key) == 1 and t.kind == "table"
    ]
    candidates.sort(key=lambda t: (USER_TABLE_NAMES.index(t.name.lower()), t.schema_name))
    return candidates[0] if candidates else None
