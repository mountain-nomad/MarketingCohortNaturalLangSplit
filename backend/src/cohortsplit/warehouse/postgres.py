"""PostgreSQL implementation of :class:`WarehouseAdapter`.

All introspection goes through :class:`ReadOnlyExecutor`, so it inherits the
read-only transaction, statement timeout and row cap. Identifiers are always
quoted with :class:`psycopg.sql.Identifier`; values are bound parameters.
"""

import re
from collections.abc import Mapping

from psycopg import sql

from cohortsplit.warehouse.errors import WarehouseQueryError
from cohortsplit.warehouse.executor import ReadOnlyExecutor
from cohortsplit.warehouse.models import (
    ColumnInfo,
    ForeignKey,
    QueryResult,
    TableKind,
    TableMetadata,
    TableRef,
    TypeCategory,
)

_KINDS: dict[str, TableKind] = {
    "r": "table",
    "p": "table",
    "v": "view",
    "m": "materialized_view",
    "f": "foreign_table",
}

_TYPE_CATEGORIES: dict[str, TypeCategory] = {
    "S": "string",
    "B": "boolean",
    "E": "enum",
    "N": "numeric",
    "D": "datetime",
}

# Introspection result sets are tiny; this cap only guards against pathological catalogs.
_CATALOG_ROW_CAP = 100_000

_LIST_SCHEMAS = """
SELECT n.nspname
FROM pg_catalog.pg_namespace n
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\\_%'
  AND has_schema_privilege(n.oid, 'USAGE')
ORDER BY n.nspname
"""

_LIST_TABLES = """
SELECT c.relname, c.relkind::text, obj_description(c.oid, 'pg_class')
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND NOT c.relispartition
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY c.relname
"""

_TABLE_OID = """
SELECT c.oid::bigint, c.reltuples::float8
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s AND c.relname = %(table)s
"""

_COLUMNS = """
SELECT a.attname,
       format_type(a.atttypid, a.atttypmod),
       COALESCE(bt.typcategory, t.typcategory)::text,
       NOT a.attnotnull,
       a.attnum::int,
       col_description(a.attrelid, a.attnum)
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
LEFT JOIN pg_catalog.pg_type bt ON t.typtype = 'd' AND bt.oid = t.typbasetype
WHERE a.attrelid = %(oid)s::oid AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""

_CONSTRAINTS = """
SELECT con.conname,
       con.contype::text,
       ARRAY(SELECT att.attname
             FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
             JOIN pg_catalog.pg_attribute att
               ON att.attrelid = con.conrelid AND att.attnum = k.attnum
             ORDER BY k.ord),
       rn.nspname,
       rc.relname,
       ARRAY(SELECT att.attname
             FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
             JOIN pg_catalog.pg_attribute att
               ON att.attrelid = con.confrelid AND att.attnum = k.attnum
             ORDER BY k.ord),
       pg_get_constraintdef(con.oid)
FROM pg_catalog.pg_constraint con
LEFT JOIN pg_catalog.pg_class rc ON rc.oid = con.confrelid
LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid = rc.relnamespace
WHERE con.conrelid = %(oid)s::oid AND con.contype IN ('p', 'f', 'c')
ORDER BY con.conname
"""

_ANY_ARRAY = re.compile(r"=\s*ANY\s*\(\s*\(?\s*ARRAY\[(?P<items>.*)\]", re.DOTALL)
_LITERAL = re.compile(r"'((?:[^']|'')*)'")


def _check_allowed_values(definition: str) -> tuple[str, ...] | None:
    """Values of ``CHECK (col = ANY (ARRAY['a'::text, ...]))``, else ``None``."""
    match = _ANY_ARRAY.search(definition)
    if match is None or " OR " in definition.upper() or " AND " in definition.upper():
        return None
    values = tuple(v.replace("''", "'") for v in _LITERAL.findall(match.group("items")))
    return values or None


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise WarehouseQueryError(f"Unexpected catalog value {value!r}; expected a number.")
    return round(value)


def _names(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise WarehouseQueryError("Unexpected catalog value; expected a list of names.")
    return tuple(str(v) for v in value)


class PostgresWarehouseAdapter:
    dialect = "postgresql"

    def __init__(self, executor: ReadOnlyExecutor) -> None:
        self._executor = executor

    def __repr__(self) -> str:
        return f"PostgresWarehouseAdapter(location={self.describe_location()!r})"

    @property
    def executor(self) -> ReadOnlyExecutor:
        return self._executor

    def describe_location(self) -> str:
        return self._executor.describe_location()

    def _catalog(self, query: str, params: Mapping[str, object] | None = None) -> QueryResult:
        return self._executor.execute(query, params, row_cap=_CATALOG_ROW_CAP)

    def test_connection(self) -> None:
        self._executor.execute("SELECT 1")

    def list_schemas(self) -> list[str]:
        return [str(row[0]) for row in self._catalog(_LIST_SCHEMAS).rows]

    def list_tables(self, schema: str) -> list[TableRef]:
        rows = self._catalog(_LIST_TABLES, {"schema": schema}).rows
        return [
            TableRef(
                schema=schema,
                name=str(name),
                kind=_KINDS[str(kind)],
                comment=None if comment is None else str(comment),
            )
            for name, kind, comment in rows
        ]

    def describe_table(self, table: TableRef) -> TableMetadata:
        found = self._catalog(_TABLE_OID, {"schema": table.schema, "table": table.name}).rows
        if not found:
            raise WarehouseQueryError(f"Table {table.qualified_name} no longer exists.")
        oid, reltuples = found[0]
        constraints = self._catalog(_CONSTRAINTS, {"oid": oid}).rows

        primary_key: tuple[str, ...] = ()
        foreign_keys: list[ForeignKey] = []
        allowed: dict[str, tuple[str, ...]] = {}
        for name, contype, cols, ref_schema, ref_table, ref_cols, definition in constraints:
            columns = _names(cols)
            if contype == "p":
                primary_key = columns
            elif contype == "f":
                foreign_keys.append(
                    ForeignKey(
                        name=str(name),
                        columns=columns,
                        referred_schema=str(ref_schema),
                        referred_table=str(ref_table),
                        referred_columns=_names(ref_cols),
                    )
                )
            elif contype == "c" and len(columns) == 1:
                values = _check_allowed_values(str(definition))
                if values is not None:
                    allowed[columns[0]] = values

        columns_info = tuple(
            ColumnInfo(
                name=str(name),
                data_type=str(data_type),
                type_category=_TYPE_CATEGORIES.get(str(category), "other"),
                nullable=bool(nullable),
                ordinal=_int(ordinal),
                comment=None if comment is None else str(comment),
                allowed_values=allowed.get(str(name)),
            )
            for name, data_type, category, nullable, ordinal, comment in self._catalog(
                _COLUMNS, {"oid": oid}
            ).rows
        )
        return TableMetadata(
            ref=table,
            columns=columns_info,
            primary_key=primary_key,
            foreign_keys=tuple(foreign_keys),
            estimated_row_count=self._row_count(
                table, -1 if reltuples is None else _int(reltuples)
            ),
        )

    def _row_count(self, table: TableRef, reltuples: int) -> int | None:
        if reltuples >= 0 and table.kind != "view":
            return reltuples
        if table.kind != "table":
            return None
        # Never analyzed: an exact count, still bounded by the statement timeout.
        query = sql.SQL("SELECT count(*) FROM {}.{}").format(
            sql.Identifier(table.schema), sql.Identifier(table.name)
        )
        rows = self._executor.execute(query.as_string(), row_cap=1).rows
        return _int(rows[0][0])

    def get_distinct_values(
        self, table: TableRef, column: str, max_distinct: int
    ) -> list[str] | None:
        if max_distinct < 1:
            raise ValueError("max_distinct must be at least 1")
        query = sql.SQL(
            "SELECT v FROM (SELECT {col}::text AS v, count(*) AS n FROM {schema}.{table} "
            "WHERE {col} IS NOT NULL GROUP BY 1) s ORDER BY n DESC, v LIMIT {limit}"
        ).format(
            col=sql.Identifier(column),
            schema=sql.Identifier(table.schema),
            table=sql.Identifier(table.name),
            limit=sql.Literal(max_distinct + 1),
        )
        rows = self._executor.execute(query.as_string(), row_cap=max_distinct + 1).rows
        if len(rows) > max_distinct:
            return None
        return [str(row[0]) for row in rows]

    def get_key_examples(self, table: TableRef, column: str, limit: int) -> list[str]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query = sql.SQL(
            "SELECT t.{col}::text AS v FROM {schema}.{table} AS t WHERE t.{col} IS NOT NULL "
            "ORDER BY t.{col} LIMIT {limit}"
        ).format(
            col=sql.Identifier(column),
            schema=sql.Identifier(table.schema),
            table=sql.Identifier(table.name),
            limit=sql.Literal(limit),
        )
        return [
            str(row[0]) for row in self._executor.execute(query.as_string(), row_cap=limit).rows
        ]

    def execute_readonly(self, sql: str, params: Mapping[str, object] | None = None) -> QueryResult:
        return self._executor.execute(sql, params)
