"""Schema inventory of the latest crawl and reference checks (FR-3).

The inventory is built from the generated ``table_schema`` docs, which hold the latest
state of every crawled schema. Business-context entries and edited use cases may only
reference tables and columns found there; anything else is refused (fail closed).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from cohortsplit.cohort_spec.draft import DraftCohortSpec, referenced_columns
from cohortsplit.semantic.context import (
    CanonicalUserIdDefinition,
    Definition,
    definition_references,
    table_of,
)

UNKNOWN_TABLE = "unknown table"
UNKNOWN_COLUMN = "unknown column"
NOT_SINGLE_PRIMARY_KEY = "not the single-column primary key of its table"


@dataclass(frozen=True)
class TableShape:
    columns: frozenset[str]
    primary_key: tuple[str, ...]


@dataclass(frozen=True)
class ReferenceProblem:
    reference: str
    problem: str


@dataclass(frozen=True)
class SchemaInventory:
    tables: Mapping[str, TableShape]

    @classmethod
    def from_table_docs(cls, contents: Iterable[Mapping[str, Any]]) -> "SchemaInventory":
        tables: dict[str, TableShape] = {}
        for content in contents:
            schema, name = content.get("schema_name"), content.get("name")
            if not schema or not name:
                continue
            columns = frozenset(
                str(c["name"])
                for c in content.get("columns") or ()
                if isinstance(c, Mapping) and c.get("name")
            )
            primary_key = tuple(str(c) for c in content.get("primary_key") or ())
            tables[f"{schema}.{name}"] = TableShape(columns=columns, primary_key=primary_key)
        return cls(tables=tables)

    def has_table(self, table: str) -> bool:
        return table in self.tables

    def has_column(self, column: str) -> bool:
        shape = self.tables.get(table_of(column))
        return shape is not None and column.rsplit(".", 1)[1] in shape.columns


def _check_references(
    tables: Iterable[str], columns: Iterable[str], inventory: SchemaInventory
) -> list[ReferenceProblem]:
    problems: list[ReferenceProblem] = []
    missing_tables = sorted({t for t in tables if not inventory.has_table(t)})
    problems += [ReferenceProblem(t, UNKNOWN_TABLE) for t in missing_tables]
    for column in sorted(set(columns)):
        if table_of(column) in missing_tables:
            continue  # reported once, as its table
        if not inventory.has_column(column):
            problems.append(ReferenceProblem(column, UNKNOWN_COLUMN))
    return problems


def check_definition(
    definition: Definition, inventory: SchemaInventory
) -> tuple[ReferenceProblem, ...]:
    """Problems with the definition's references, sorted (empty = all resolve)."""
    tables, columns = definition_references(definition)
    problems = _check_references(tables, columns, inventory)
    if not problems and isinstance(definition, CanonicalUserIdDefinition):
        shape = inventory.tables[table_of(definition.column)]
        if shape.primary_key != (definition.column.rsplit(".", 1)[1],):
            problems.append(ReferenceProblem(definition.column, NOT_SINGLE_PRIMARY_KEY))
    return tuple(problems)


def check_spec(spec: DraftCohortSpec, inventory: SchemaInventory) -> tuple[ReferenceProblem, ...]:
    """Problems with a draft cohort spec's references (edited/confirmed use cases)."""
    columns = referenced_columns(spec)
    tables = {spec.entity.table} | {table_of(c) for c in columns}
    return tuple(_check_references(tables, columns, inventory))
