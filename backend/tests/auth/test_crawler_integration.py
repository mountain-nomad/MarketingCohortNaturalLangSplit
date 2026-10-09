"""Auth <-> crawler integration (FR-A4 missing grants, crawler ruling R2, FR-A5 crawler runs)."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, delete, insert
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.audit.service import Actor, AuditService
from cohortsplit.auth.crawler_integration import (
    AuditCrawlHook,
    CrawlStoreColumnInventory,
    RoleExportGrants,
    get_column_inventory,
)
from cohortsplit.crawler import cli as crawl_cli
from cohortsplit.crawler.service import CrawlFailedError
from cohortsplit.crawler.store import CrawlStore
from cohortsplit.crawler.tables import crawl_runs, semantic_docs
from tests.auth.conftest import AuditReader
from tests.auth.helpers import ApiClient, FakeClock, make_role
from tests.constants import APPDB_PASSWORD, WAREHOUSE_PASSWORD

PHONE = "public.users.phone"
EMAIL = "public.users.email"


class FakeInventory:
    def __init__(self, columns: frozenset[str] | None) -> None:
        self._columns = columns

    def columns(self) -> frozenset[str] | None:
        return self._columns


def _role(admin: ApiClient, role_id: int) -> dict[str, object]:
    response = admin.get(f"/api/admin/roles/{role_id}")
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    return body


# --- FR-A4: grants on columns absent from the latest crawl are marked missing ------------


def test_role_marks_grants_missing_from_latest_crawl(
    app: FastAPI, admin_client: ApiClient, db: Session, clock: FakeClock
) -> None:
    app.dependency_overrides[get_column_inventory] = lambda: FakeInventory(
        frozenset({EMAIL, "public.orders.status"})
    )
    role = make_role(db, "Contact", ["cohort.export"], [EMAIL, PHONE], now=clock.now)

    body = _role(admin_client, role.id)

    assert body["export_columns"] == [EMAIL, PHONE]
    assert body["missing_export_columns"] == [PHONE]
    assert body["column_inventory"] == "available"
    listed = admin_client.get("/api/admin/roles").json()["items"]
    assert [r["missing_export_columns"] for r in listed if r["id"] == role.id] == [[PHONE]]


def test_grant_status_unknown_before_any_crawl(
    app: FastAPI, admin_client: ApiClient, db: Session, clock: FakeClock
) -> None:
    app.dependency_overrides[get_column_inventory] = lambda: FakeInventory(None)
    role = make_role(db, "Contact", ["cohort.export"], [PHONE], now=clock.now)

    body = _role(admin_client, role.id)

    assert body["missing_export_columns"] == []
    assert body["column_inventory"] == "unavailable"


def test_missing_marks_do_not_block_saving_grants(app: FastAPI, admin_client: ApiClient) -> None:
    # A grant may be prepared before the column exists; it is inert until then.
    app.dependency_overrides[get_column_inventory] = lambda: FakeInventory(frozenset({EMAIL}))

    response = admin_client.post(
        "/api/admin/roles", json={"name": "Future", "export_columns": [PHONE]}
    )

    assert response.status_code == 201
    assert response.json()["missing_export_columns"] == [PHONE]


@pytest.fixture
def crawl_tables(engine: Engine) -> Iterator[CrawlStore]:
    if engine.dialect.name != "postgresql":
        pytest.skip("crawler tables are PostgreSQL-only (JSONB); covered by the postgres run")
    with engine.begin() as conn:
        conn.execute(delete(semantic_docs))
        conn.execute(delete(crawl_runs))
    yield CrawlStore(engine)
    with engine.begin() as conn:
        conn.execute(delete(semantic_docs))
        conn.execute(delete(crawl_runs))


def _table_doc(schema: str, table: str, columns: list[str], origin: str = "generated") -> dict:  # type: ignore[type-arg]
    return {
        "doc_key": f"table:{schema}.{table}",
        "kind": "table_schema",
        "origin": origin,
        "content": {
            "schema_name": schema,
            "name": table,
            "kind": "table",
            "comment": None,
            "columns": [
                {
                    "name": c,
                    "data_type": "text",
                    "type_category": "string",
                    "nullable": True,
                    "comment": None,
                    "allowed_values": None,
                }
                for c in columns
            ],
            "primary_key": [],
            "foreign_keys": [],
        },
    }


def test_crawl_store_inventory_reads_generated_table_docs(
    engine: Engine, crawl_tables: CrawlStore
) -> None:
    assert CrawlStoreColumnInventory(crawl_tables).columns() is None  # never crawled

    with engine.begin() as conn:
        conn.execute(insert(crawl_runs).values(status="succeeded", triggered_by="cli"))
        conn.execute(
            insert(semantic_docs),
            [
                _table_doc("public", "users", ["user_id", "email"]),
                _table_doc("sales", "orders", ["order_id"]),
                # Human-authored docs describe intent, not the warehouse: ignored.
                _table_doc("public", "users", ["phone"], origin="human"),
            ],
        )

    assert CrawlStoreColumnInventory(crawl_tables).columns() == frozenset(
        {"public.users.user_id", "public.users.email", "sales.orders.order_id"}
    )


def test_role_api_uses_the_crawl_store_by_default(
    admin_client: ApiClient, engine: Engine, crawl_tables: CrawlStore, db: Session, clock: FakeClock
) -> None:
    with engine.begin() as conn:
        conn.execute(insert(crawl_runs).values(status="succeeded", triggered_by="cli"))
        conn.execute(insert(semantic_docs), [_table_doc("public", "users", ["email"])])
    role = make_role(db, "Contact", ["cohort.export"], [EMAIL, PHONE], now=clock.now)

    body = _role(admin_client, role.id)

    assert body["missing_export_columns"] == [PHONE]
    assert body["column_inventory"] == "available"


def test_inventory_unavailable_when_crawler_tables_unreadable(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite test database has no crawler tables")

    assert CrawlStoreColumnInventory(CrawlStore(engine)).columns() is None


# --- crawler ruling R2: export-granted columns are never sampled --------------------------


def test_role_export_grants_is_union_of_all_roles(
    engine: Engine, db: Session, clock: FakeClock
) -> None:
    make_role(db, "A", [], [PHONE], now=clock.now)
    make_role(db, "B", [], [PHONE, EMAIL], now=clock.now)
    make_role(db, "C", ["cohort.create"], [], now=clock.now)

    grants = RoleExportGrants(sessionmaker(engine)).export_granted_columns()

    assert grants == frozenset({PHONE, EMAIL})


# --- FR-A5: crawler runs are audited ------------------------------------------------------


def test_audit_crawl_hook_records_one_event_per_run(
    engine: Engine, clock: FakeClock, audit_events: AuditReader
) -> None:
    hook = AuditCrawlHook(AuditService(sessionmaker(engine)), Actor.cli(), clock=clock)

    hook.crawl_started(11, "cli")
    hook.crawl_succeeded(11, "cli", "a" * 64)
    hook.crawl_started(12, "cli")
    hook.crawl_failed(12, "cli", "warehouse unreachable at wh:5432/ecommerce")

    events = audit_events.of("crawler.run")
    assert [(e.target_type, e.target_id, e.outcome, e.actor_type) for e in events] == [
        ("crawl_run", "11", "success", "cli"),
        ("crawl_run", "12", "error", "cli"),
    ]
    assert events[0].metadata_["content_hash"] == "a" * 64
    assert events[1].metadata_["error"] == "warehouse unreachable at wh:5432/ecommerce"


def test_audit_crawl_hook_truncates_long_errors(
    engine: Engine, clock: FakeClock, audit_events: AuditReader
) -> None:
    hook = AuditCrawlHook(AuditService(sessionmaker(engine)), Actor.cli(), clock=clock)

    hook.crawl_failed(1, "cli", "x" * 5000)

    [event] = audit_events.of("crawler.run")
    assert len(event.metadata_["error"]) <= 500


# --- `cohortsplit crawl` wires both hooks -------------------------------------------------


def test_crawl_cli_passes_grants_and_audit_hook(
    engine: Engine,
    db: Session,
    clock: FakeClock,
    clean_env: pytest.MonkeyPatch,
    audit_events: AuditReader,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_role(db, "Contact", ["cohort.export"], [PHONE], now=clock.now)
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", APPDB_PASSWORD)
    clean_env.setenv(
        "COHORTSPLIT_WAREHOUSE_DSN",
        f"postgresql://cohortsplit_ro:{WAREHOUSE_PASSWORD}@127.0.0.1:9/ecommerce",
    )
    clean_env.setattr(crawl_cli, "create_appdb_engine", lambda _settings: engine)
    # The CLI disposes its engine; keep the shared test database alive.
    clean_env.setattr(engine, "dispose", lambda: None)
    seen: dict[str, object] = {}

    def fake_run_crawl(adapter, store, *, settings, export_grants, audit, triggered_by):  # type: ignore[no-untyped-def]
        seen["grants"] = export_grants.export_granted_columns()
        audit.crawl_started(5, triggered_by)
        audit.crawl_failed(5, triggered_by, "boom")
        raise CrawlFailedError("boom")

    clean_env.setattr(crawl_cli, "run_crawl", fake_run_crawl)

    code = crawl_cli.run(crawl_cli.argparse.Namespace(json=False))

    assert code == 1
    assert seen["grants"] == frozenset({PHONE})
    [event] = audit_events.of("crawler.run")
    assert (event.actor_type, event.outcome, event.target_id) == ("cli", "error", "5")
