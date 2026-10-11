"""AST validation of a SQL statement before it reaches the warehouse (FR-6, AC-11, ruling C3).

Independent of the compiler and the LLM, and fail closed: the statement is parsed with
sqlglot and admitted only if

* it is exactly one statement, with no ``;``, comments or NUL bytes;
* its root is a plain ``SELECT`` (no set operations, ``WITH``, ``INTO`` or locking);
* every AST node is of an **allowlisted** type (so DML/DDL/DCL, ``COPY``, unparsed
  commands, window functions and table functions are refused);
* every function is an allowlisted, side-effect-free builtin (``pg_sleep``, ``dblink``,
  ``lo_*``, ``set_config``, ``nextval`` … are anonymous functions and refused);
* every table is schema-qualified and in the allowlist (the crawl inventory);
* parameters are psycopg named placeholders only.
"""

import re
from collections.abc import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECT = "postgresql"

_PLACEHOLDER = re.compile(r"%%|%\(([A-Za-z_][A-Za-z0-9_]{0,62})\)s|%")

ALLOWED_NODES: frozenset[type[exp.Expression]] = frozenset(
    {
        exp.Select,
        exp.From,
        exp.Join,
        exp.Where,
        exp.Order,
        exp.Ordered,
        exp.Limit,
        exp.Distinct,
        exp.Table,
        exp.TableAlias,
        exp.Subquery,
        exp.Alias,
        exp.Identifier,
        exp.Column,
        exp.Star,
        exp.Literal,
        exp.Boolean,
        exp.Null,
        exp.Placeholder,
        exp.Paren,
        exp.And,
        exp.Or,
        exp.Not,
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.In,
        exp.Is,
        exp.Exists,
        exp.Add,
        exp.Sub,
        exp.Mul,
        exp.Interval,
        exp.Var,
        exp.Cast,
        exp.DataType,
        exp.Count,
        exp.Sum,
        exp.Coalesce,
    }
)
# Function node types among the allowlist (all side-effect free).
ALLOWED_FUNCTIONS: frozenset[type[exp.Expression]] = frozenset(
    t for t in ALLOWED_NODES if issubclass(t, exp.Func)
)

_STATEMENT_NAMES: dict[type[exp.Expression], str] = {
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Delete: "DELETE",
    exp.Merge: "MERGE",
    exp.Drop: "DROP",
    exp.Alter: "ALTER",
    exp.TruncateTable: "TRUNCATE",
    exp.Create: "CREATE",
    exp.Grant: "GRANT",
    exp.Copy: "COPY",
    exp.Command: "command",
    exp.Into: "SELECT ... INTO",
    exp.Lock: "row locking (FOR UPDATE/SHARE)",
    exp.With: "WITH (common table expressions)",
    exp.Union: "UNION",
    exp.Intersect: "INTERSECT",
    exp.Except: "EXCEPT",
}


class SqlValidationError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(f"SQL validation failed: {reason}")
        self.reason = reason


def _to_sqlglot(sql: str) -> str:
    """psycopg ``%(name)s`` placeholders -> ``:name``; ``%%`` -> ``%``; any other ``%`` refused."""

    def replace(match: re.Match[str]) -> str:
        if match.group(0) == "%%":
            return "%"
        if match.group(1) is None:
            raise SqlValidationError("stray '%' (only named parameters are allowed)")
        return f":{match.group(1)}"

    return _PLACEHOLDER.sub(replace, sql)


def _describe(node: exp.Expression) -> str:
    for kind, name in _STATEMENT_NAMES.items():
        if isinstance(node, kind):
            if isinstance(node, exp.Command):
                return f"{str(node.this).upper()} statements"
            return f"{name} statements" if name.isupper() else name
    if isinstance(node, exp.Anonymous):
        return f"function {node.name}"
    if isinstance(node, exp.Func):
        return f"function {type(node).__name__.upper()}"
    return f"{type(node).__name__} expressions"


def validate_select(sql: str, *, allowed_tables: Iterable[str], dialect: str = DIALECT) -> None:
    """Raise :class:`SqlValidationError` unless ``sql`` is one allowlisted read-only SELECT."""
    if dialect != DIALECT:
        raise SqlValidationError(f"dialect {dialect!r} is not supported")
    if not sql.strip():
        raise SqlValidationError("empty statement")
    if "\x00" in sql:
        raise SqlValidationError("NUL byte in statement")
    if ";" in sql:
        raise SqlValidationError("';' is not allowed (exactly one statement)")
    text = _to_sqlglot(sql)
    try:
        tokens = sqlglot.Dialect.get_or_raise("postgres").tokenize(text)
        if any(token.comments for token in tokens):
            raise SqlValidationError("comments are not allowed")
        statements = sqlglot.parse(text, read="postgres")
    except SqlglotError as exc:
        raise SqlValidationError(f"statement could not be parsed ({type(exc).__name__})") from None
    present = [s for s in statements if s is not None]
    if len(present) != 1:
        raise SqlValidationError("exactly one statement is allowed")
    [root] = present
    if not isinstance(root, exp.Select):
        raise SqlValidationError(f"{_describe(root)} are not allowed; only SELECT is")
    allowed = frozenset(allowed_tables)
    for node in root.walk():
        if type(node) not in ALLOWED_NODES:
            raise SqlValidationError(f"{_describe(node)} are not allowed")
        if isinstance(node, exp.Table):
            _check_table(node, allowed)
        elif isinstance(node, exp.DataType) and not node.is_type(exp.DataType.Type.TIMESTAMPTZ):
            raise SqlValidationError(f"casts to {node.sql('postgres')} are not allowed")
        elif isinstance(node, exp.Join) and (node.args.get("on") is None or node.args.get("kind")):
            raise SqlValidationError("only inner joins with an ON condition are allowed")
        elif isinstance(node, exp.Column) and (node.args.get("db") or node.args.get("catalog")):
            raise SqlValidationError("schema-qualified column references are not allowed")


def _check_table(table: exp.Table, allowed: frozenset[str]) -> None:
    if not isinstance(table.this, exp.Identifier):
        raise SqlValidationError("table functions are not allowed")
    if table.args.get("catalog"):
        raise SqlValidationError("catalog-qualified tables are not allowed")
    schema = table.db
    if not schema:
        raise SqlValidationError(f"table {table.name} must be schema-qualified")
    name = f"{schema}.{table.name}"
    if name not in allowed:
        raise SqlValidationError(f"table {name} is not in the allowlist")
