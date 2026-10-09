"""Demo warehouse is seeded and ``cohortsplit_ro`` is read-only at the database level.

Seeds MVP AC-12: any write attempted directly as the configured DB user is refused
by the database itself, not only by application-level validation.
"""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integration

# Table names from upstream harryho/db-samples@9bd103f pgsql/ecommerce.sql.
EXPECTED_TABLES = {
    "users",
    "addresses",
    "categories",
    "products",
    "product_variants",
    "product_images",
    "inventory",
    "inventory_movements",
    "carriers",
    "carts",
    "cart_items",
    "orders",
    "order_items",
    "order_status_history",
    "payments",
    "shipments",
}

WRITE_STATEMENTS = {
    "insert": (
        "INSERT INTO users (email, password_hash, first_name, last_name) "
        "VALUES ('probe@example.com', 'x', 'probe', 'probe')"
    ),
    "update": "UPDATE users SET first_name = 'probe' WHERE user_id = (SELECT min(user_id) FROM users)",
    "delete": "DELETE FROM order_status_history WHERE order_id = (SELECT min(order_id) FROM orders)",
    "create_table": "CREATE TABLE public.cohortsplit_probe (id int)",
    "create_temp_table": "CREATE TEMP TABLE cohortsplit_probe_tmp (id int)",
    "drop_table": "DROP TABLE users",
    "truncate": "TRUNCATE order_status_history",
    "alter_table": "ALTER TABLE users ADD COLUMN cohortsplit_probe int",
}

REFUSED = (errors.InsufficientPrivilege, errors.ReadOnlySqlTransaction)


@pytest.fixture
def ro_conn(warehouse_ro_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(warehouse_ro_dsn, connect_timeout=5) as conn:
        yield conn
        conn.rollback()


def test_connected_as_read_only_role(ro_conn: psycopg.Connection) -> None:
    row = ro_conn.execute(
        "SELECT current_user, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
        "FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    assert row == ("cohortsplit_ro", False, False, False, False)

    setting = ro_conn.execute("SHOW default_transaction_read_only").fetchone()
    assert setting == ("on",)


def test_ecommerce_tables_exist(ro_conn: psycopg.Connection) -> None:
    rows = ro_conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    ).fetchall()

    assert EXPECTED_TABLES <= {r[0] for r in rows}


def test_select_works_and_users_seeded(ro_conn: psycopg.Connection) -> None:
    row = ro_conn.execute("SELECT count(*) FROM users").fetchone()
    assert row is not None
    assert row[0] > 0

    joined = ro_conn.execute(
        "SELECT count(*) FROM orders o JOIN users u ON u.user_id = o.user_id"
    ).fetchone()
    assert joined is not None
    assert joined[0] > 0


@pytest.mark.parametrize("statement", WRITE_STATEMENTS.values(), ids=WRITE_STATEMENTS.keys())
def test_writes_refused_by_database(ro_conn: psycopg.Connection, statement: str) -> None:
    with pytest.raises(REFUSED):
        ro_conn.execute(statement)


@pytest.mark.parametrize("statement", WRITE_STATEMENTS.values(), ids=WRITE_STATEMENTS.keys())
def test_writes_refused_even_after_session_switches_to_read_write(
    ro_conn: psycopg.Connection, statement: str
) -> None:
    """default_transaction_read_only is user-overridable; grants must still refuse writes."""
    ro_conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
    ro_conn.commit()
    ro_conn.execute("SET TRANSACTION READ WRITE")

    with pytest.raises(errors.InsufficientPrivilege):
        ro_conn.execute(statement)
