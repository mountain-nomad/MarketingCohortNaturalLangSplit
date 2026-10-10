"""Deterministic PostgreSQL compilation of a resolved cohort (FR-6, AC-10).

* Same resolved cohort (spec + semantic version) and dialect -> byte-identical SQL.
  Parameters are numbered in traversal order; nothing depends on time or randomness.
* Identifiers come only from the snapshot's schema metadata and are always quoted.
* Every literal is a bound parameter (``%(pN)s``, psycopg style); relative windows use the
  bound execution time ``%(as_of)s`` (UTC), which is excluded from :func:`sql_hash`.
* One row per canonical user: rows come from the canonical user table (a single-column
  primary key, ruling S2) and are ``DISTINCT``. Related rows match only through equality
  on the user key, so rows with a NULL user reference (guest carts) never match.
* Attribute comparisons are two-valued (``COALESCE(..., FALSE)``): a NULL never satisfies a
  comparison and ``NOT`` inverts exactly.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from cohortsplit.cohort_spec.v1 import canonical_json
from cohortsplit.compiler.resolve import (
    JsonScalar,
    RAttribute,
    RAttributeWindow,
    ResolvedCohort,
    RFilter,
    RGroup,
    RNode,
    RNot,
    RRelated,
    RWindow,
    ValueLookup,
)

DIALECT = "postgresql"
AS_OF_PARAM = "as_of"
USER_ALIAS = "u"
ID_COLUMN = "canonical_user_id"

_COMPARISON = {"=": "=", "!=": "<>", ">": ">", ">=": ">=", "<": "<", "<=": "<="}


class UnsupportedDialectError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    params: Mapping[str, JsonScalar]


def quote_ident(name: str) -> str:
    """A double-quoted identifier (``"`` doubled; ``%`` doubled for psycopg)."""
    if not name or "\x00" in name:
        raise ValueError("invalid identifier")
    return '"' + name.replace('"', '""').replace("%", "%%") + '"'


def _table(qualified: str) -> str:
    schema, name = qualified.split(".", 1)
    return f"{quote_ident(schema)}.{quote_ident(name)}"


def sql_hash(query: CompiledQuery) -> str:
    """SHA-256 of the SQL and its definition parameters (execution time excluded)."""
    params = {k: v for k, v in query.params.items() if k != AS_OF_PARAM}
    return hashlib.sha256(canonical_json({"sql": query.sql, "params": params})).hexdigest()


class _Builder:
    def __init__(self, as_of: datetime | None) -> None:
        self.params: dict[str, JsonScalar] = {}
        self._count = 0
        self._as_of = as_of

    def param(self, value: JsonScalar) -> str:
        self._count += 1
        name = f"p{self._count}"
        self.params[name] = value
        return f"%({name})s"

    def as_of(self) -> str:
        if self._as_of is None:
            raise ValueError("relative windows need the execution time (as_of)")
        self.params[AS_OF_PARAM] = self._as_of.isoformat()
        return f"CAST(%({AS_OF_PARAM})s AS TIMESTAMPTZ)"

    # -- predicates ----------------------------------------------------------------------

    def literal(self, value: JsonScalar, category: str) -> str:
        placeholder = self.param(value)
        return f"CAST({placeholder} AS TIMESTAMPTZ)" if category == "datetime" else placeholder

    def filter(self, value_filter: RFilter, alias: str) -> str:
        column = f"{alias}.{quote_ident(value_filter.column)}"
        operator = value_filter.operator
        if operator == "is_null":
            return f"{column} IS NULL"
        if operator == "is_not_null":
            return f"{column} IS NOT NULL"
        if operator in ("in", "not_in"):
            items = ", ".join(self.literal(v, value_filter.category) for v in value_filter.values)
            return f"{column} {'IN' if operator == 'in' else 'NOT IN'} ({items})"
        value = self.literal(value_filter.values[0], value_filter.category)
        return f"{column} {_COMPARISON[operator]} {value}"

    def window(self, window: RWindow, alias: str) -> str:
        column = f"{alias}.{quote_ident(window.column)}"
        if window.last_days is not None:
            days = self.param(window.last_days)
            return (
                f"{column} >= {self.as_of()} - {days} * INTERVAL '1 day' "
                f"AND {column} <= {self.as_of()}"
            )
        bounds: list[str] = []
        if window.start is not None:
            bounds.append(f"{column} >= {self.literal(window.start.isoformat(), 'datetime')}")
        if window.end is not None:
            bounds.append(f"{column} < {self.literal(window.end.isoformat(), 'datetime')}")
        return " AND ".join(bounds)

    def related(self, related: RRelated, outer: str, user_key: str, user_table: str) -> str:
        if related.hops:
            first = related.hops[0]
            source = f"{_table(first.to_table)} AS t1"
            for index, hop in enumerate(related.hops[1:], start=2):
                joined = f"{_table(hop.to_table)} AS t{index}"
                on = (
                    f"t{index}.{quote_ident(hop.to_column)} = "
                    f"t{index - 1}.{quote_ident(hop.from_column)}"
                )
                source += f" JOIN {joined} ON {on}"
            conditions = [
                f"t1.{quote_ident(first.to_column)} = {outer}.{quote_ident(first.from_column)}"
            ]
        else:
            source = f"{_table(user_table)} AS t0"
            conditions = [f"t0.{quote_ident(user_key)} = {outer}.{quote_ident(user_key)}"]
        conditions += [self.filter(f, f"t{f.position}") for f in related.filters]
        if related.window is not None:
            conditions.append(self.window(related.window, f"t{related.window.position}"))
        body = f"FROM {source} WHERE {' AND '.join(conditions)}"
        aggregate = related.aggregate
        if aggregate is None:
            return f"EXISTS (SELECT 1 {body})"
        target = f"t{related.target}"
        keys = ", ".join(f"{target}.{quote_ident(k)}" for k in related.target_key)
        operator = _COMPARISON[aggregate.operator]
        if aggregate.function == "count":
            threshold = self.param(aggregate.value)
            return (
                f"(SELECT COUNT(*) FROM (SELECT DISTINCT {keys} {body}) AS agg) "  # noqa: S608
                f"{operator} {threshold}"
            )
        assert aggregate.column is not None  # noqa: S101 (resolver guarantees a sum column)
        threshold = self.param(aggregate.value)
        return (
            f'COALESCE((SELECT SUM(agg."v") FROM (SELECT DISTINCT {keys}, '  # noqa: S608
            f'{target}.{quote_ident(aggregate.column)} AS "v" {body}) AS agg), 0) '
            f"{operator} {threshold}"
        )

    def node(self, node: RNode, resolved: ResolvedCohort, *, nested: bool) -> str:
        match node:
            case RAttribute(filter=value_filter):
                predicate = self.filter(value_filter, USER_ALIAS)
                if value_filter.operator in ("is_null", "is_not_null"):
                    return predicate
                return f"COALESCE({predicate}, FALSE)"
            case RAttributeWindow(window=window):
                return f"COALESCE({self.window(window, USER_ALIAS)}, FALSE)"
            case RRelated():
                return self.related(node, USER_ALIAS, resolved.user_key, resolved.user_table)
            case RNot(child=child):
                return f"NOT ({self.node(child, resolved, nested=False)})"
            case RGroup(operator=operator, children=children):
                text = f" {operator} ".join(self.node(c, resolved, nested=True) for c in children)
                return f"({text})" if nested and len(children) > 1 else text
        raise TypeError(f"unknown node {type(node).__name__}")  # pragma: no cover

    def where(self, resolved: ResolvedCohort) -> str:
        parts: list[str] = []
        if resolved.where is not None:
            nested = bool(resolved.exclusions)
            parts.append(self.node(resolved.where, resolved, nested=nested))
        for exclusion in resolved.exclusions:
            parts.append(
                "NOT ("
                + self.related(
                    exclusion.related, USER_ALIAS, resolved.user_key, resolved.user_table
                )
                + ")"
            )
        return f" WHERE {' AND '.join(parts)}" if parts else ""


def _check_dialect(dialect: str) -> None:
    if dialect != DIALECT:
        raise UnsupportedDialectError(f"dialect {dialect!r} is not supported (only {DIALECT})")


def _id(resolved: ResolvedCohort) -> str:
    return f"{USER_ALIAS}.{quote_ident(resolved.user_key)}"


def _from(resolved: ResolvedCohort) -> str:
    return f"FROM {_table(resolved.user_table)} AS {USER_ALIAS}"


def compile_cohort(
    resolved: ResolvedCohort, *, as_of: datetime, dialect: str = DIALECT
) -> CompiledQuery:
    """The membership query: one row per canonical user id, ordered."""
    _check_dialect(dialect)
    builder = _Builder(as_of)
    where = builder.where(resolved)
    sql = (
        f"SELECT DISTINCT {_id(resolved)} AS {quote_ident(ID_COLUMN)} {_from(resolved)}"
        f"{where} ORDER BY 1"
    )
    return CompiledQuery(sql, builder.params)


def _user_columns(resolved: ResolvedCohort, columns: Sequence[str]) -> list[str]:
    names: list[str] = []
    for column in columns:
        table, _, name = column.rpartition(".")
        if table != resolved.user_table or name not in resolved.user_columns:
            raise ValueError(f"{column} is not a column of the user table {resolved.user_table}")
        names.append(name)
    return names


def compile_sample(
    resolved: ResolvedCohort,
    *,
    columns: Sequence[str],
    limit: int,
    as_of: datetime,
    dialect: str = DIALECT,
) -> CompiledQuery:
    """The first ``limit`` members with the requested user-table columns."""
    _check_dialect(dialect)
    if limit < 1:
        raise ValueError("limit must be at least 1")
    names = _user_columns(resolved, columns)
    builder = _Builder(as_of)
    where = builder.where(resolved)
    selected = [f"{_id(resolved)} AS {quote_ident(ID_COLUMN)}"] + [
        f"{USER_ALIAS}.{quote_ident(n)} AS {quote_ident(c)}"
        for n, c in zip(names, columns, strict=True)
    ]
    sql = (
        f"SELECT DISTINCT {', '.join(selected)} {_from(resolved)}{where} "
        f"ORDER BY 1 LIMIT {int(limit)}"
    )
    return CompiledQuery(sql, builder.params)


def compile_null_counts(
    resolved: ResolvedCohort, *, columns: Sequence[str], as_of: datetime, dialect: str = DIALECT
) -> CompiledQuery:
    """Total members and, per column, how many members have NULL there."""
    _check_dialect(dialect)
    names = _user_columns(resolved, columns)
    builder = _Builder(as_of)
    where = builder.where(resolved)
    selected = ['COUNT(*) AS "total"'] + [
        f"COUNT(*) - COUNT({USER_ALIAS}.{quote_ident(n)}) AS {quote_ident(f'null_{i}')}"
        for i, n in enumerate(names, start=1)
    ]
    return CompiledQuery(f"SELECT {', '.join(selected)} {_from(resolved)}{where}", builder.params)


def compile_value_lookup(lookup: ValueLookup, *, dialect: str = DIALECT) -> CompiledQuery:
    """Which of the literal values exist in their column (unknown product/category names)."""
    _check_dialect(dialect)
    table, _, column = lookup.column.rpartition(".")
    builder = _Builder(None)
    items = ", ".join(builder.param(v) for v in lookup.values)
    sql = (
        f"SELECT DISTINCT t.{quote_ident(column)} FROM {_table(table)} AS t "  # noqa: S608
        f"WHERE t.{quote_ident(column)} IN ({items})"
    )
    return CompiledQuery(sql, builder.params)
