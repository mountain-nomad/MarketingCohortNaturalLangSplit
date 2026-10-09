"""Schema inventory from the latest crawl and reference checks. STUB."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from cohortsplit.cohort_spec.draft import DraftCohortSpec
from cohortsplit.semantic.context import Definition


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
        raise NotImplementedError

    def has_table(self, table: str) -> bool:
        raise NotImplementedError

    def has_column(self, column: str) -> bool:
        raise NotImplementedError


def check_definition(
    definition: Definition, inventory: SchemaInventory
) -> tuple[ReferenceProblem, ...]:
    raise NotImplementedError


def check_spec(spec: DraftCohortSpec, inventory: SchemaInventory) -> tuple[ReferenceProblem, ...]:
    raise NotImplementedError
