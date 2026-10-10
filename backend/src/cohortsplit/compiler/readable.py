"""Human-readable interpretation of a resolved cohort (FR-5), e.g.

``has addresses with country_code = DE AND has carts with status = abandoned AND NOT has
paid_order``

Deterministic: derived only from the resolved cohort. Columns of the condition's own table
are shown by name, others as ``table.column``; business terms by their key; definitions
of metrics are not repeated (they are shown in Business context).
"""

from datetime import datetime

from cohortsplit.compiler.resolve import (
    RAggregate,
    RAttribute,
    RAttributeWindow,
    ResolvedCohort,
    RFilter,
    RGroup,
    RNode,
    RNot,
    RRelated,
    RWindow,
)

OPERATORS = {
    "=": "=",
    "!=": "≠",
    ">": ">",
    ">=": "≥",
    "<": "<",
    "<=": "≤",
    "in": "in",
    "not_in": "not in",
    "is_null": "is null",
    "is_not_null": "is not null",
}


def _column(table: str, column: str, home: str) -> str:
    return column if table == home else f"{table.split('.', 1)[1]}.{column}"


def _filter(value_filter: RFilter, home: str) -> str:
    name = _column(value_filter.table, value_filter.column, home)
    operator = OPERATORS[value_filter.operator]
    if value_filter.operator in ("is_null", "is_not_null"):
        return f"{name} {operator}"
    if value_filter.operator in ("in", "not_in"):
        return f"{name} {operator} ({', '.join(value_filter.display)})"
    return f"{name} {operator} {value_filter.display[0]}"


def _moment(value: datetime) -> str:
    if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
        return value.date().isoformat()
    return value.isoformat()


def _window(window: RWindow, home: str) -> str:
    name = _column(window.table, window.column, home)
    if window.last_days is not None:
        return f"{name} within {window.last_days} days"
    if window.start is not None and window.end is not None:
        return f"{name} from {_moment(window.start)} to {_moment(window.end)}"
    if window.start is not None:
        return f"{name} from {_moment(window.start)}"
    assert window.end is not None  # noqa: S101 (absolute windows have a bound)
    return f"{name} before {_moment(window.end)}"


def _number(value: int | float) -> str:
    return str(value)


def _related(related: RRelated) -> str:
    home = related.target_table
    conditions = [_filter(f, home) for f in related.filters[related.hidden_filters :]]
    if related.window is not None:
        conditions.append(_window(related.window, home))
    aggregate: RAggregate | None = related.aggregate
    if aggregate is None:
        text = f"has {related.label}"
        return f"{text} with {' and '.join(conditions)}" if conditions else text
    where = f" where {' and '.join(conditions)}" if conditions else ""
    operator = OPERATORS[aggregate.operator]
    if aggregate.function == "count":
        return f"count({related.label}{where}) {operator} {_number(aggregate.value)}"
    return (
        f"sum({aggregate.column} of {related.label}{where}) {operator} "
        f"{_number(aggregate.value)}"
    )


def _node(node: RNode, user_table: str, *, nested: bool) -> str:
    match node:
        case RAttribute(filter=value_filter):
            return _filter(value_filter, user_table)
        case RAttributeWindow(window=window):
            return _window(window, user_table)
        case RRelated():
            return _related(node)
        case RNot(child=child):
            return f"NOT {_node(child, user_table, nested=True)}"
        case RGroup(operator=operator, children=children):
            text = f" {operator} ".join(_node(c, user_table, nested=True) for c in children)
            return f"({text})" if nested and len(children) > 1 else text
    raise TypeError(f"unknown node {type(node).__name__}")  # pragma: no cover


def readable(resolved: ResolvedCohort) -> str:
    exclusions = [f"NOT {e.key}" for e in resolved.exclusions]
    if resolved.where is None:
        return " AND ".join(["all users", *exclusions])
    where = resolved.where
    nested = bool(exclusions) and isinstance(where, RGroup) and where.operator == "OR"
    return " AND ".join([_node(where, resolved.user_table, nested=nested), *exclusions])
