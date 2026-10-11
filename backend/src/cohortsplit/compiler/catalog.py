"""Schema catalog of the semantic snapshot: tables, columns and FK edges the compiler may use.

Built only from the snapshot's raw schema metadata (BR-7), so the compiler can never
reference a table, column or join the crawler did not discover.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from cohortsplit.semantic.snapshot import PromptTable

INTEGER_TYPES = frozenset({"smallint", "integer", "bigint"})


@dataclass(frozen=True)
class ColumnShape:
    table: str
    name: str
    data_type: str
    category: str
    allowed_values: tuple[str, ...] | None
    sample_values: tuple[str, ...]

    @property
    def qualified(self) -> str:
        return f"{self.table}.{self.name}"

    @property
    def domain(self) -> tuple[str, ...] | None:
        """Every value the column can hold, when known: CHECK values, else the complete
        low-cardinality sample (the crawler only stores samples when it read every value)."""
        if self.allowed_values is not None:
            return self.allowed_values
        return self.sample_values or None

    @property
    def is_integer(self) -> bool:
        return self.data_type in INTEGER_TYPES


@dataclass(frozen=True)
class TableShape:
    name: str
    columns: Mapping[str, ColumnShape]
    primary_key: tuple[str, ...]

    @property
    def schema(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def short_name(self) -> str:
        return self.name.split(".", 1)[1]


@dataclass(frozen=True, order=True)
class Edge:
    """A single-column FK relationship, walked from ``from_column`` to ``to_column``."""

    from_column: str
    to_column: str

    @property
    def from_table(self) -> str:
        return self.from_column.rsplit(".", 1)[0]

    @property
    def to_table(self) -> str:
        return self.to_column.rsplit(".", 1)[0]

    def describe(self) -> str:
        return f"{self.from_column} -> {self.to_column}"


class Catalog:
    def __init__(self, tables: Iterable[PromptTable]) -> None:
        self.tables: dict[str, TableShape] = {}
        for table in sorted(tables, key=lambda t: t.qualified_name):
            columns = {
                c.name: ColumnShape(
                    table=table.qualified_name,
                    name=c.name,
                    data_type=c.data_type,
                    category=c.type_category,
                    allowed_values=c.allowed_values,
                    sample_values=c.sample_values,
                )
                for c in table.columns
            }
            self.tables[table.qualified_name] = TableShape(
                table.qualified_name, columns, table.primary_key
            )
        edges: set[Edge] = set()
        for table in tables:
            for fk in table.foreign_keys:
                if len(fk.columns) != 1 or len(fk.referred_columns) != 1:
                    continue  # composite keys are not joinable in spec v1 (fail closed)
                child = f"{table.qualified_name}.{fk.columns[0]}"
                parent = f"{fk.referred_table}.{fk.referred_columns[0]}"
                if self.column(child) is None or self.column(parent) is None:
                    continue
                edges |= {Edge(child, parent), Edge(parent, child)}
        self.edges: dict[str, tuple[Edge, ...]] = {
            name: tuple(sorted(e for e in edges if e.from_table == name)) for name in self.tables
        }
        self._edge_set = frozenset(edges)

    def table(self, name: str) -> TableShape | None:
        return self.tables.get(name)

    def column(self, qualified: str) -> ColumnShape | None:
        table_name, _, column = qualified.rpartition(".")
        table = self.tables.get(table_name)
        return None if table is None else table.columns.get(column)

    def is_join(self, from_column: str, to_column: str) -> bool:
        return Edge(from_column, to_column) in self._edge_set

    def shortest_paths(
        self, start: str, goal: str, *, max_hops: int, avoid: frozenset[str] = frozenset()
    ) -> list[tuple[Edge, ...]]:
        """Every simple FK path of minimal length from ``start`` to ``goal`` (sorted)."""
        frontier: list[tuple[Edge, ...]] = [()]
        for _ in range(max_hops):
            found: list[tuple[Edge, ...]] = []
            grown: list[tuple[Edge, ...]] = []
            for path in frontier:
                here = path[-1].to_table if path else start
                visited = {start, *(e.to_table for e in path)}
                for edge in self.edges.get(here, ()):
                    if edge.to_table in visited or edge.to_table in avoid:
                        continue
                    candidate = (*path, edge)
                    (found if edge.to_table == goal else grown).append(candidate)
            if found:
                return sorted(found)
            frontier = grown
        return []
