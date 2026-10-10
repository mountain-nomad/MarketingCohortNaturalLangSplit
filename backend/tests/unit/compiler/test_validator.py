"""AST validation of every SQL statement before execution (AC-11, BR-4, ruling C3).

The validator is independent of the compiler and of the LLM: it parses the statement with
sqlglot and only admits a single read-only SELECT made of an allowlisted set of node types
and functions over allowlisted schema-qualified tables.
"""

import pytest

from cohortsplit.compiler.validator import SqlValidationError, validate_select

TABLES = frozenset({"public.users", "public.orders", "public.carts"})

OK = (
    'SELECT DISTINCT u."user_id" AS "canonical_user_id" FROM "public"."users" AS u '
    'WHERE EXISTS (SELECT 1 FROM "public"."orders" AS t1 WHERE t1."user_id" = u."user_id" '
    'AND t1."status" IN (%(p1)s, %(p2)s)) ORDER BY 1'
)


def rejected(sql: str) -> str:
    with pytest.raises(SqlValidationError) as info:
        validate_select(sql, allowed_tables=TABLES)
    return info.value.reason


def test_compiled_shape_is_accepted() -> None:
    validate_select(OK, allowed_tables=TABLES)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO public.users (user_id) VALUES (1)",
        "UPDATE public.users SET is_active = false",
        "DELETE FROM public.users",
        "DROP TABLE public.users",
        "ALTER TABLE public.users ADD COLUMN x int",
        "TRUNCATE public.users",
        "CREATE TABLE public.x (a int)",
        "GRANT SELECT ON public.users TO PUBLIC",
        "REVOKE SELECT ON public.users FROM PUBLIC",
    ],
    ids=["insert", "update", "delete", "drop", "alter", "truncate", "create", "grant", "revoke"],
)
def test_ac11_mutation_and_administration_statements_rejected(sql: str) -> None:
    assert rejected(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM public.users AS u; SELECT 2 FROM public.users AS u",
        "SELECT 1 FROM public.users AS u; DROP TABLE public.users",
        "SELECT 1 FROM public.users AS u;",
    ],
    ids=["two-selects", "select-then-drop", "trailing-semicolon"],
)
def test_ac11_multiple_statements_rejected(sql: str) -> None:
    assert rejected(sql)


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        (
            "WITH d AS (DELETE FROM public.users RETURNING user_id) SELECT d.user_id FROM d",
            "cte-with-dml",
        ),
        ("WITH d AS (SELECT 1 AS a) SELECT d.a FROM d", "cte"),
        ("SELECT u.user_id INTO public.copy FROM public.users AS u", "select-into"),
        ("COPY public.users TO STDOUT", "copy"),
        ("COPY (SELECT 1) TO PROGRAM 'id'", "copy-program"),
        ("SELECT pg_sleep(10)", "pg-sleep"),
        ("SELECT u.user_id FROM public.users AS u WHERE pg_sleep(10) IS NULL", "pg-sleep-where"),
        ("SELECT dblink('host=x', 'DELETE FROM t')", "dblink"),
        ("SELECT lo_import('/etc/passwd')", "lo-import"),
        ("SELECT lo_export(1, '/tmp/x')", "lo-export"),
        ("SELECT pg_read_file('/etc/passwd')", "pg-read-file"),
        ("SELECT set_config('statement_timeout', '0', false)", "set-config"),
        ("SELECT current_setting('is_superuser')", "current-setting"),
        ("SELECT nextval('public.seq')", "nextval"),
        ("SELECT pg_terminate_backend(1)", "terminate"),
        ("SELECT pg_advisory_lock(1)", "advisory-lock"),
        ("SELECT u.user_id FROM public.users AS u FOR UPDATE", "for-update"),
        ("SELECT u.user_id FROM public.users AS u -- trailing comment", "line-comment"),
        ("SELECT u.user_id /* hidden */ FROM public.users AS u", "block-comment"),
        ("SELECT u.user_id FROM public.secrets AS u", "table-not-allowlisted"),
        ("SELECT u.user_id FROM users AS u", "unqualified-table"),
        ("SELECT u.usename FROM pg_catalog.pg_user AS u", "system-catalog"),
        ("SELECT t.table_name FROM information_schema.tables AS t", "information-schema"),
        ("SELECT u.user_id FROM public.users AS u UNION SELECT 1", "union"),
        ("SELECT u.user_id FROM public.users AS u WHERE u.user_id = $1", "positional-param"),
        ("SELECT * FROM generate_series(1, 10)", "table-function"),
        ("EXPLAIN ANALYZE SELECT 1", "explain-analyze"),
        ("CALL public.proc()", "call"),
        ("DO $$ BEGIN DELETE FROM public.users; END $$", "do-block"),
        ("VACUUM public.users", "vacuum"),
        ("SET ROLE postgres", "set-role"),
        ("LOCK TABLE public.users", "lock"),
        ("LISTEN chan", "listen"),
        ("SELECT u.user_id FROM public.users AS u WHERE u.x = '%s'", "stray-percent"),
        ("", "empty"),
        ("SELECT 1 FROM public.users AS u\x00", "nul"),
    ],
    ids=lambda value: value if isinstance(value, str) and " " not in value else None,
)
def test_ac11_bypass_attempts_rejected(sql: str, why: str) -> None:
    assert rejected(sql), why


def test_reason_names_the_offending_construct() -> None:
    assert "DELETE" in rejected("DELETE FROM public.users").upper()
    assert "pg_sleep" in rejected("SELECT pg_sleep(10)")
    assert "public.secrets" in rejected("SELECT u.user_id FROM public.secrets AS u")


def test_only_postgresql_is_supported() -> None:
    with pytest.raises(SqlValidationError):
        validate_select(OK, allowed_tables=TABLES, dialect="mysql")
