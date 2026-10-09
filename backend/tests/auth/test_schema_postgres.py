"""Migration ``0002_auth`` on PostgreSQL: seed data, constraints, append-only audit, ORM parity."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError

from cohortsplit.auth.catalog import ADMIN_ROLE_NAME, PERMISSION_CATALOG
from cohortsplit.orm import Base

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def pg(pg_database: URL) -> Iterator[Connection]:
    engine: Engine = create_engine(pg_database)
    with engine.connect() as conn:
        trans = conn.begin()
        yield conn
        trans.rollback()
    engine.dispose()


def _insert_user(conn: Connection, email: str) -> int:
    row = conn.execute(
        text(
            "INSERT INTO users (email, display_name, password_hash, created_at, updated_at) "
            "VALUES (:email, 'x', 'h', :now, :now) RETURNING id"
        ),
        {"email": email, "now": NOW},
    ).one()
    return int(row[0])


def test_permission_catalog_seeded_from_code(pg: Connection) -> None:
    rows = dict(pg.execute(text("SELECT key, description FROM permissions")).all())

    assert rows == {p.key: p.description for p in PERMISSION_CATALOG}


def test_admin_system_role_seeded_without_permission_rows(pg: Connection) -> None:
    roles = pg.execute(text("SELECT id, name, is_system FROM roles")).all()
    [admin] = [r for r in roles if r.is_system]
    assert admin.name == ADMIN_ROLE_NAME

    rows = pg.execute(
        text("SELECT count(*) FROM role_permissions WHERE role_id = :id"), {"id": admin.id}
    ).scalar_one()
    assert rows == 0  # implicit: every permission, including future ones


def test_only_one_system_role_allowed(pg: Connection) -> None:
    with pytest.raises(IntegrityError):
        pg.execute(
            text(
                "INSERT INTO roles (name, is_system, created_at, updated_at) "
                "VALUES ('Admin 2', true, :now, :now)"
            ),
            {"now": NOW},
        )


def test_email_must_be_stored_lowercase(pg: Connection) -> None:
    with pytest.raises(IntegrityError):
        _insert_user(pg, "Mixed@Example.com")


def test_email_unique(pg: Connection) -> None:
    _insert_user(pg, "dup@example.com")
    with pytest.raises(IntegrityError):
        _insert_user(pg, "dup@example.com")


def test_role_name_unique_case_insensitive(pg: Connection) -> None:
    insert = text("INSERT INTO roles (name, created_at, updated_at) VALUES (:name, :now, :now)")
    pg.execute(insert, {"name": "Marketer", "now": NOW})
    with pytest.raises(IntegrityError):
        pg.execute(insert, {"name": "MARKETER", "now": NOW})


def _insert_event(conn: Connection) -> int:
    return int(
        conn.execute(
            text(
                "INSERT INTO audit_events (occurred_at, actor_type, action, outcome) "
                "VALUES (:now, 'cli', 'test.event', 'success') RETURNING id"
            ),
            {"now": NOW},
        ).scalar_one()
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'tampered' WHERE id = :id",
        "DELETE FROM audit_events WHERE id = :id",
        "TRUNCATE audit_events",
    ],
    ids=["update", "delete", "truncate"],
)
def test_audit_events_are_append_only_in_database(pg: Connection, statement: str) -> None:
    event_id = _insert_event(pg)

    with pytest.raises(DBAPIError, match="append-only"):
        pg.execute(text(statement), {"id": event_id})


def test_audit_actor_type_and_outcome_are_constrained(pg: Connection) -> None:
    with pytest.raises(IntegrityError):
        pg.execute(
            text(
                "INSERT INTO audit_events (occurred_at, actor_type, action, outcome) "
                "VALUES (:now, 'cli', 'x', 'maybe')"
            ),
            {"now": NOW},
        )


def test_orm_models_match_the_migration(pg: Connection) -> None:
    import cohortsplit.audit.models
    import cohortsplit.auth.models  # noqa: F401  (register tables on Base.metadata)

    context = MigrationContext.configure(pg, opts={"compare_type": True})
    diff = compare_metadata(context, Base.metadata)

    # Expression indexes (lower(name)) cannot be compared by autogenerate; ignore those.
    relevant = [
        d
        for d in diff
        if not (
            isinstance(d, tuple)
            and d[0] in ("add_index", "remove_index")
            and d[1].name == "uq_roles_name_lower"
        )
    ]
    assert relevant == []
