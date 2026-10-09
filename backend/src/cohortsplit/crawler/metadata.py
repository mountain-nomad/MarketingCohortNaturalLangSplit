"""Collect schema documentation and policy-bounded data profiles from a warehouse."""

import logging
from collections.abc import Sequence

from cohortsplit.crawler.catalog import (
    ColumnDoc,
    ColumnProfile,
    ForeignKeyDoc,
    TableDoc,
    TableProfile,
    WarehouseCatalog,
)
from cohortsplit.crawler.entities import detect_user_table
from cohortsplit.crawler.sampling import SamplingPolicy
from cohortsplit.warehouse.adapter import WarehouseAdapter
from cohortsplit.warehouse.errors import (
    QueryTimeoutError,
    WarehousePermissionError,
    WarehouseQueryError,
)
from cohortsplit.warehouse.models import TableMetadata

logger = logging.getLogger(__name__)

KEY_EXAMPLE_LIMIT = 1

# Per-column sampling failures are recorded, not fatal. Connectivity failures are.
_COLUMN_ERRORS = (QueryTimeoutError, WarehousePermissionError, WarehouseQueryError)


def _table_doc(meta: TableMetadata) -> TableDoc:
    return TableDoc(
        schema_name=meta.ref.schema,
        name=meta.ref.name,
        kind=meta.ref.kind,
        comment=meta.ref.comment,
        columns=tuple(
            ColumnDoc(
                name=c.name,
                data_type=c.data_type,
                type_category=c.type_category,
                nullable=c.nullable,
                comment=c.comment,
                allowed_values=c.allowed_values,
            )
            for c in sorted(meta.columns, key=lambda c: c.ordinal)
        ),
        primary_key=meta.primary_key,
        foreign_keys=tuple(
            ForeignKeyDoc(
                name=f.name,
                columns=f.columns,
                referred_table=f"{f.referred_schema}.{f.referred_table}",
                referred_columns=f.referred_columns,
            )
            for f in sorted(meta.foreign_keys, key=lambda f: f.name)
        ),
    )


def _sample_column(
    adapter: WarehouseAdapter, policy: SamplingPolicy, meta: TableMetadata, column: str
) -> ColumnProfile:
    try:
        values = adapter.get_distinct_values(meta.ref, column, policy.max_distinct)
    except _COLUMN_ERRORS as exc:
        logger.warning(
            "sampling failed table=%s column=%s error=%s",
            meta.ref.qualified_name,
            column,
            type(exc).__name__,
        )
        return ColumnProfile(
            name=column, sampled=False, reason=f"sampling failed ({type(exc).__name__})"
        )
    rows = meta.estimated_row_count
    if values is None:
        reason = f"not low-cardinality (more than {policy.max_distinct} distinct values)"
        return ColumnProfile(name=column, sampled=False, reason=reason)
    if rows is not None and len(values) * 2 > rows:
        # Near-unique values in a small table look like identifiers, not categories.
        reason = "near-unique values (distinct values exceed half the rows); not sampled"
        return ColumnProfile(name=column, sampled=False, reason=reason)
    return ColumnProfile(
        name=column,
        sampled=True,
        reason=f"low-cardinality ({len(values)} distinct values)",
        values=tuple(values),
    )


def _key_examples(
    adapter: WarehouseAdapter, policy: SamplingPolicy, meta: TableMetadata
) -> tuple[str, ...]:
    if len(meta.primary_key) != 1:
        return ()
    key = next((c for c in meta.columns if c.name == meta.primary_key[0]), None)
    if key is None or not policy.decide_key_example(meta.ref, key).allowed:
        return ()
    try:
        return tuple(adapter.get_key_examples(meta.ref, key.name, KEY_EXAMPLE_LIMIT))
    except _COLUMN_ERRORS as exc:
        logger.warning(
            "key examples failed table=%s error=%s", meta.ref.qualified_name, type(exc).__name__
        )
        return ()


def _profile(
    adapter: WarehouseAdapter,
    policy: SamplingPolicy,
    meta: TableMetadata,
    *,
    is_user_table: bool,
) -> TableProfile:
    columns: list[ColumnProfile] = []
    for column in sorted(meta.columns, key=lambda c: c.ordinal):
        decision = policy.decide(meta.ref, column)
        if decision.allowed:
            columns.append(_sample_column(adapter, policy, meta, column.name))
        else:
            columns.append(ColumnProfile(name=column.name, sampled=False, reason=decision.reason))
    return TableProfile(
        table=meta.ref.qualified_name,
        estimated_row_count=meta.estimated_row_count,
        columns=tuple(columns),
        key_examples=() if is_user_table else _key_examples(adapter, policy, meta),
    )


def collect_catalog(
    adapter: WarehouseAdapter,
    policy: SamplingPolicy,
    *,
    schemas: Sequence[str] = (),
    user_table: str | None = None,
) -> WarehouseCatalog:
    """Discover tables/columns/keys/row counts and sample permitted low-cardinality columns.

    ``schemas`` limits the crawl (empty = every schema the role may use).
    ``user_table`` is the configured user entity table ("schema.table"); when ``None``
    it is detected by name. Its key values are never read as examples.
    """
    crawl_schemas = sorted(set(schemas)) if schemas else adapter.list_schemas()
    metas = [
        adapter.describe_table(ref)
        for schema in crawl_schemas
        for ref in sorted(adapter.list_tables(schema))
    ]
    docs = tuple(_table_doc(m) for m in metas)
    user = detect_user_table(docs, user_table)
    user_name = user.qualified_name if user is not None else None
    profiles = tuple(
        _profile(adapter, policy, meta, is_user_table=meta.ref.qualified_name == user_name)
        for meta in metas
    )
    return WarehouseCatalog(dialect=adapter.dialect, tables=docs, profiles=profiles)
