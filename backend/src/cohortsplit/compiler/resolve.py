"""Deterministic semantic resolution of a cohort spec against one semantic snapshot.

Everything the compiler will use is checked here, independent of the LLM (BR-2, FR-4):

* the canonical user id must be defined (ruling S2, no fallback);
* every table, column and FK hop must exist in the snapshot's schema metadata;
* business terms (metrics, time windows) must be defined in the snapshot's business
  context — stale entries are absent from the snapshot and therefore undefined;
* literals are coerced to the column's type; values of enumerable columns (CHECK values or
  the complete low-cardinality sample) must be known.

Outcomes: :class:`ResolvedCohort`, :class:`Unresolved` (ask: undefined terms, unknown
values) or :class:`Invalid` (refuse: references the LLM invented or that cannot be joined).
"""

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cohortsplit.cohort_spec.common import Filter, normalize_term, table_of
from cohortsplit.cohort_spec.v1 import (
    MAX_PATH,
    AbsoluteWindow,
    Aggregate,
    AllOf,
    AnyOf,
    AttributeCondition,
    AttributeTimeWindowCondition,
    CohortSpecV1,
    Condition,
    Join,
    MetricCondition,
    Not,
    RelatedCondition,
    RelativeWindow,
    TermWindow,
)
from cohortsplit.compiler.catalog import Catalog, ColumnShape, Edge
from cohortsplit.semantic.snapshot import PromptBusinessContext, SemanticContextSnapshot

JsonScalar = str | int | float | bool

CANONICAL_USER_ID_MISSING = (
    "No canonical user identifier is defined. Define the canonical user identifier in "
    "Business context before creating cohorts."
)
EQUALITY_OPERATORS = frozenset({"=", "!=", "in", "not_in"})
NULL_OPERATORS = frozenset({"is_null", "is_not_null"})
_INT = re.compile(r"^[+-]?\d{1,18}$")
_DECIMAL = re.compile(r"^[+-]?\d{1,18}\.\d{1,18}$")
MAX_SUGGESTIONS = 3
MAX_KNOWN_SHOWN = 20


class CanonicalUserIdMissingError(Exception):
    def __init__(self) -> None:
        super().__init__(CANONICAL_USER_ID_MISSING)


# -- resolved model ----------------------------------------------------------------------


@dataclass(frozen=True)
class Hop:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass(frozen=True)
class RFilter:
    position: int  # 0 = the user table (attributes / self relation), i = path table t{i}
    table: str
    column: str
    operator: str
    values: tuple[JsonScalar, ...]
    category: str
    display: tuple[str, ...]


@dataclass(frozen=True)
class RWindow:
    position: int
    table: str
    column: str
    last_days: int | None = None
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True)
class RAggregate:
    function: str
    operator: str
    value: int | float
    column: str | None = None  # sum: column of the target table


@dataclass(frozen=True)
class RAttribute:
    filter: RFilter


@dataclass(frozen=True)
class RAttributeWindow:
    window: RWindow


@dataclass(frozen=True)
class RRelated:
    label: str
    hops: tuple[Hop, ...]  # empty: the user table itself (alias t0)
    target: int  # index of the target table (0 when hops is empty)
    target_table: str
    target_key: tuple[str, ...]
    filters: tuple[RFilter, ...]
    hidden_filters: int  # leading filters that come from a business definition
    window: RWindow | None
    aggregate: RAggregate | None


@dataclass(frozen=True)
class RGroup:
    operator: str  # "AND" | "OR"
    children: tuple["RNode", ...]


@dataclass(frozen=True)
class RNot:
    child: "RNode"


RNode = RAttribute | RAttributeWindow | RRelated | RGroup | RNot


@dataclass(frozen=True)
class RExclusion:
    key: str
    related: RRelated


@dataclass(frozen=True)
class ValueLookup:
    """Literal values of a column without a known domain, checked against the warehouse."""

    column: str
    values: tuple[JsonScalar, ...]


@dataclass(frozen=True)
class ResolvedCohort:
    user_table: str
    user_key: str
    user_columns: Mapping[str, ColumnShape]
    where: RNode | None
    exclusions: tuple[RExclusion, ...]
    lookups: tuple[ValueLookup, ...]
    tables: frozenset[str]


@dataclass(frozen=True)
class UnknownValue:
    column: str
    value: str
    suggestions: tuple[str, ...]
    known: tuple[str, ...]


@dataclass(frozen=True)
class Unresolved:
    question: str
    unknown_terms: tuple[str, ...]
    unknown_values: tuple[UnknownValue, ...]


@dataclass(frozen=True)
class Invalid:
    problems: tuple[str, ...]


Resolution = ResolvedCohort | Unresolved | Invalid


# -- resolution ----------------------------------------------------------------------------


def _term_key(term: str) -> str:
    return normalize_term(term.replace("_", " "))


@dataclass
class _Context:
    catalog: Catalog
    user_table: str
    user_key: str
    terms: Mapping[str, PromptBusinessContext]
    problems: list[str] = field(default_factory=list)
    unknown_terms: list[str] = field(default_factory=list)
    wrong_kind: dict[str, tuple[str, str]] = field(default_factory=dict)  # actual, wanted
    unknown_values: list[UnknownValue] = field(default_factory=list)
    lookups: list[ValueLookup] = field(default_factory=list)
    tables: set[str] = field(default_factory=set)

    def problem(self, message: str) -> None:
        if message not in self.problems:
            self.problems.append(message)

    def term(self, name: str, kind: str) -> PromptBusinessContext | None:
        key = _term_key(name)
        entry = self.terms.get(key)
        if entry is None or entry.kind != kind:
            if key not in self.unknown_terms:
                self.unknown_terms.append(key)
            if entry is not None:
                self.wrong_kind[key] = (entry.kind, kind)
            return None
        return entry


def _terms(snapshot: SemanticContextSnapshot) -> dict[str, PromptBusinessContext]:
    found: dict[str, PromptBusinessContext] = {}
    for entry in snapshot.business_context:
        for name in (entry.key, *entry.synonyms):
            found.setdefault(_term_key(name), entry)
    return found


def _suggest(value: str, domain: Sequence[str]) -> tuple[str, ...]:
    folded = {d.casefold(): d for d in domain}
    close = difflib.get_close_matches(value.casefold(), list(folded), n=MAX_SUGGESTIONS, cutoff=0.6)
    return tuple(folded[c] for c in close)


def _coerce(ctx: _Context, column: ColumnShape, value: Any) -> JsonScalar | None:
    """The literal as the column's type, or None after recording why it cannot be."""
    category = column.category
    bad = f"{value!r} is not a valid value for {column.qualified} ({column.data_type})"
    if category in ("string", "enum"):
        if isinstance(value, bool):
            ctx.problem(bad)
            return None
        return str(value) if isinstance(value, int | float) else value
    if category == "numeric":
        if isinstance(value, bool):
            ctx.problem(bad)
            return None
        if isinstance(value, int | float):
            return value
        text = str(value).strip()
        if _INT.match(text):
            return int(text)
        if _DECIMAL.match(text):
            return float(text)
        ctx.problem(bad)
        return None
    if category == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        ctx.problem(bad)
        return None
    if category == "datetime":
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.strip())
            except ValueError:
                ctx.problem(bad)
                return None
            parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
            return parsed.astimezone(UTC).isoformat()
        ctx.problem(bad)
        return None
    ctx.problem(f"filters on {column.qualified} ({column.data_type}) are not supported")
    return None


def _display(value: JsonScalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value.endswith("T00:00:00+00:00"):
        return value[:10]
    return str(value)


def _filter(
    ctx: _Context, value_filter: Filter, position: int, *, trusted: bool = False
) -> RFilter | None:
    """``trusted``: a value from a reviewed business definition, not typed by the marketer;
    it is coerced but never value-checked or looked up (a value with no rows matches nothing)."""
    column = ctx.catalog.column(value_filter.column)
    if column is None:
        ctx.problem(f"unknown column {value_filter.column}")
        return None
    raw: tuple[Any, ...]
    if value_filter.operator in NULL_OPERATORS:
        raw = ()
    elif isinstance(value_filter.value, tuple):
        raw = value_filter.value
    else:
        raw = (value_filter.value,)
    coerced = [_coerce(ctx, column, v) for v in raw]
    if any(v is None for v in coerced):
        return None
    values = tuple(v for v in coerced if v is not None)
    if value_filter.operator in EQUALITY_OPERATORS and not trusted:
        domain = column.domain if column.category in ("string", "enum") else None
        if domain is not None:
            for value in values:
                if str(value) not in domain:
                    ctx.unknown_values.append(
                        UnknownValue(
                            column=column.qualified,
                            value=str(value),
                            suggestions=_suggest(str(value), domain),
                            known=tuple(domain),
                        )
                    )
        elif column.category in ("string", "enum") or (
            column.category == "numeric" and column.is_integer
        ):
            lookup = ValueLookup(column.qualified, values)
            if lookup not in ctx.lookups:
                ctx.lookups.append(lookup)
    return RFilter(
        position=position,
        table=column.table,
        column=column.name,
        operator=value_filter.operator,
        values=values,
        category=column.category,
        display=tuple(_display(v) for v in values),
    )


def _window(
    ctx: _Context,
    window: RelativeWindow | AbsoluteWindow | TermWindow,
    *,
    default_column: str | None,
    positions: Mapping[str, list[int]],
) -> RWindow | None:
    last_days: int | None = None
    start: datetime | None = None
    end: datetime | None = None
    column_name = window.column
    if isinstance(window, TermWindow):
        entry = ctx.term(window.term, "time_window")
        if entry is None:
            return None
        last_days = int(entry.definition["last_days"])
        column_name = column_name or entry.definition.get("column")
    elif isinstance(window, RelativeWindow):
        last_days = window.last_days
    else:
        start, end = window.start, window.end
    column_name = column_name or default_column
    if column_name is None:
        ctx.problem(
            "the time window needs a date column: name one in the request or define a time "
            "column for the business term"
        )
        return None
    column = ctx.catalog.column(column_name)
    if column is None:
        ctx.problem(f"unknown column {column_name}")
        return None
    if column.category != "datetime":
        ctx.problem(f"{column_name} is not a date column")
        return None
    position = _position(ctx, column_name, positions)
    if position is None:
        return None
    return RWindow(position, column.table, column.name, last_days, start, end)


def _position(ctx: _Context, column: str, positions: Mapping[str, list[int]]) -> int | None:
    found = positions.get(table_of(column), [])
    if not found:
        ctx.problem(f"{column} is not on the relationship path of its condition")
        return None
    if len(found) > 1:
        ctx.problem(f"{column} is ambiguous: its table appears more than once on the path")
        return None
    return found[0]


def _hops_from_path(ctx: _Context, path: Sequence[Join]) -> tuple[Hop, ...] | None:
    if table_of(path[0].from_column) != ctx.user_table:
        ctx.problem(f"relationship paths must start at the user table {ctx.user_table}")
        return None
    hops: list[Hop] = []
    for join in path:
        if not ctx.catalog.is_join(join.from_column, join.to_column):
            ctx.problem(f"no foreign key links {Edge(join.from_column, join.to_column).describe()}")
            return None
        hops.append(_hop(Edge(join.from_column, join.to_column)))
    return tuple(hops)


def _hop(edge: Edge) -> Hop:
    return Hop(
        edge.from_table,
        edge.from_column.rsplit(".", 1)[1],
        edge.to_table,
        edge.to_column.rsplit(".", 1)[1],
    )


def _auto_hops(ctx: _Context, table: str, label: str) -> tuple[Hop, ...] | None:
    paths = ctx.catalog.shortest_paths(ctx.user_table, table, max_hops=MAX_PATH)
    if not paths:
        ctx.problem(f"no relationship links {ctx.user_table} to {table} ({label})")
        return None
    if len(paths) > 1:
        options = "; ".join(" , ".join(e.describe() for e in p) for p in paths)
        ctx.problem(
            f"the relationship from {ctx.user_table} to {table} ({label}) is ambiguous "
            f"({options}); give an explicit path"
        )
        return None
    return tuple(_hop(e) for e in paths[0])


def _related(
    ctx: _Context,
    *,
    label: str,
    table: str,
    path: Sequence[Join] | None,
    base_filters: Sequence[Filter],
    filters: Sequence[Filter],
    window: RelativeWindow | AbsoluteWindow | TermWindow | None,
    aggregate: Aggregate | None,
    default_time_column: str | None = None,
    default_value_column: str | None = None,
) -> RRelated | None:
    target_shape = ctx.catalog.table(table)
    if target_shape is None:
        ctx.problem(f"unknown table {table}")
        return None
    hops: tuple[Hop, ...] | None
    if table == ctx.user_table and path is None:
        hops = ()
    elif path is None:
        hops = _auto_hops(ctx, table, label)
    else:
        hops = _hops_from_path(ctx, path)
    if hops is None:
        return None
    positions: dict[str, list[int]] = {}
    if not hops:
        positions[ctx.user_table] = [0]
    for index, hop in enumerate(hops, start=1):
        positions.setdefault(hop.to_table, []).append(index)
    if table not in positions:
        ctx.problem(f"the path does not reach {table}")
        return None
    target = positions[table][0]
    ctx.tables |= {ctx.user_table, *positions}

    resolved_filters: list[RFilter] = []
    trusted_count = len(base_filters)
    for index, value_filter in enumerate((*base_filters, *filters)):
        if ctx.catalog.column(value_filter.column) is None:
            ctx.problem(f"unknown column {value_filter.column}")
            continue
        position = _position(ctx, value_filter.column, positions)
        if position is None:
            continue
        resolved = _filter(ctx, value_filter, position, trusted=index < trusted_count)
        if resolved is not None:
            resolved_filters.append(resolved)

    resolved_window = (
        None
        if window is None
        else _window(ctx, window, default_column=default_time_column, positions=positions)
    )
    resolved_aggregate: RAggregate | None = None
    if aggregate is not None:
        resolved_aggregate = _aggregate(
            ctx, aggregate, label=label, table=table, default_column=default_value_column
        )
    if not target_shape.primary_key and aggregate is not None:
        ctx.problem(f"{table} has no primary key, so its rows cannot be counted or summed")
    return RRelated(
        label=label,
        hops=hops,
        target=target,
        target_table=table,
        target_key=target_shape.primary_key,
        filters=tuple(resolved_filters),
        hidden_filters=len(base_filters),
        window=resolved_window,
        aggregate=resolved_aggregate,
    )


def _aggregate(
    ctx: _Context, aggregate: Aggregate, *, label: str, table: str, default_column: str | None
) -> RAggregate | None:
    if aggregate.function == "count":
        return RAggregate("count", aggregate.operator, aggregate.value)
    column_name = aggregate.column or default_column
    if column_name is None:
        ctx.problem(f"sum over {label} needs a value column (none given and none defined)")
        return None
    column = ctx.catalog.column(column_name)
    if column is None:
        ctx.problem(f"unknown column {column_name}")
        return None
    if column.table != table:
        ctx.problem(f"sum column {column_name} must belong to {table}")
        return None
    if column.category != "numeric":
        ctx.problem(f"sum column {column_name} is not numeric")
        return None
    return RAggregate("sum", aggregate.operator, aggregate.value, column.name)


def _node(ctx: _Context, condition: Condition) -> RNode | None:
    match condition:
        case AttributeCondition(filter=value_filter):
            column = ctx.catalog.column(value_filter.column)
            if column is None:
                ctx.problem(f"unknown column {value_filter.column}")
                return None
            if column.table != ctx.user_table:
                ctx.problem(
                    f"attribute {value_filter.column} is not a column of the user table "
                    f"{ctx.user_table}; use a related condition"
                )
                return None
            resolved = _filter(ctx, value_filter, 0)
            return None if resolved is None else RAttribute(resolved)
        case AttributeTimeWindowCondition(window=window):
            if window.column is not None and table_of(window.column) != ctx.user_table:
                ctx.problem(f"{window.column} is not a column of the user table")
                return None
            resolved_window = _window(
                ctx, window, default_column=None, positions={ctx.user_table: [0]}
            )
            return None if resolved_window is None else RAttributeWindow(resolved_window)
        case RelatedCondition():
            if condition.table == ctx.user_table:
                ctx.problem("conditions on the user table itself are attribute conditions")
                return None
            return _related(
                ctx,
                label=condition.table.split(".", 1)[1],
                table=condition.table,
                path=condition.path,
                base_filters=(),
                filters=condition.filters,
                window=condition.time_window,
                aggregate=condition.aggregate,
            )
        case MetricCondition():
            entry = ctx.term(condition.term, "metric")
            if entry is None:
                if isinstance(condition.time_window, TermWindow):
                    ctx.term(condition.time_window.term, "time_window")  # ask for both
                return None
            definition = entry.definition
            return _related(
                ctx,
                label=entry.key,
                table=str(definition["table"]),
                path=condition.path,
                base_filters=[Filter.model_validate(f) for f in definition.get("filters") or ()],
                filters=condition.filters,
                window=condition.time_window,
                aggregate=condition.aggregate,
                default_time_column=definition.get("time_column"),
                default_value_column=definition.get("value_column"),
            )
        case AllOf(conditions=children) | AnyOf(conditions=children):
            resolved_children = [_node(ctx, c) for c in children]
            if any(c is None for c in resolved_children):
                return None
            operator = "AND" if isinstance(condition, AllOf) else "OR"
            return RGroup(operator, tuple(c for c in resolved_children if c is not None))
        case Not(condition=child):
            resolved_child = _node(ctx, child)
            return None if resolved_child is None else RNot(resolved_child)
    raise TypeError(f"unknown condition {type(condition).__name__}")  # pragma: no cover


def _exclusions(ctx: _Context, snapshot: SemanticContextSnapshot) -> list[RExclusion]:
    found: list[RExclusion] = []
    for entry in snapshot.business_context:
        if entry.kind != "exclusion":
            continue
        related = _related(
            ctx,
            label=entry.key,
            table=str(entry.definition["table"]),
            path=None,
            base_filters=[Filter.model_validate(f) for f in entry.definition.get("filters") or ()],
            filters=(),
            window=None,
            aggregate=None,
        )
        if related is not None:
            found.append(RExclusion(entry.key, related))
    return found


def _question(ctx: _Context) -> str:
    parts: list[str] = []
    undefined = [t for t in ctx.unknown_terms if t not in ctx.wrong_kind]
    if undefined:
        names = ", ".join(f"'{t}'" for t in undefined)
        parts.append(
            f"What do you mean by {names}? There is no definition in Business context; "
            "add it there (or rephrase the request with defined terms)."
        )
    for term, (actual, wanted) in ctx.wrong_kind.items():
        parts.append(
            f"'{term}' is defined in Business context but is not a {wanted.replace('_', ' ')} "
            f"(it is a {actual.replace('_', ' ')}); rephrase the request."
        )
    for unknown in ctx.unknown_values:
        text = f"No value '{unknown.value}' for {unknown.column}."
        if unknown.suggestions:
            text += " Did you mean: " + ", ".join(unknown.suggestions) + "?"
        else:
            text += " Known values: " + ", ".join(unknown.known[:MAX_KNOWN_SHOWN]) + "."
        parts.append(text)
    return " ".join(parts)


def resolve(spec: CohortSpecV1, snapshot: SemanticContextSnapshot) -> Resolution:
    if snapshot.canonical_user_id is None:
        raise CanonicalUserIdMissingError()
    catalog = Catalog(snapshot.tables)
    user_table = table_of(snapshot.canonical_user_id)
    user_key = snapshot.canonical_user_id.rsplit(".", 1)[1]
    user_shape = catalog.table(user_table)
    if user_shape is None or user_key not in user_shape.columns:
        raise CanonicalUserIdMissingError()
    ctx = _Context(catalog, user_table, user_key, _terms(snapshot))
    ctx.tables.add(user_table)
    where = None if spec.where is None else _node(ctx, spec.where)
    exclusions = _exclusions(ctx, snapshot)
    if ctx.unknown_terms or ctx.unknown_values:
        return Unresolved(
            question=_question(ctx),
            unknown_terms=tuple(ctx.unknown_terms),
            unknown_values=tuple(ctx.unknown_values),
        )
    if ctx.problems or (spec.where is not None and where is None):
        return Invalid(tuple(ctx.problems) or ("the cohort definition could not be resolved",))
    return ResolvedCohort(
        user_table=user_table,
        user_key=user_key,
        user_columns=user_shape.columns,
        where=where,
        exclusions=tuple(exclusions),
        lookups=tuple(ctx.lookups),
        tables=frozenset(ctx.tables),
    )
