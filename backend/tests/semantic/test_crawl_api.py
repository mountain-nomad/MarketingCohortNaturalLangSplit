"""Crawl through the API (crawler.run): real read-only warehouse role, export-grant provider,
audit hook, one crawl at a time; re-crawls never overwrite human context (AC-25) and flag
confirmed use cases whose columns disappeared (AC-26)."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.crawler.sampling import NoExportGrants
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.crawler.tables import crawl_runs
from cohortsplit.semantic import repository as semantic_repo
from cohortsplit.semantic.crawl import CRAWL_RUN_LOCK_KEY, CrawlEnvironment, get_crawl_environment
from cohortsplit.semantic.provider import SemanticContextProvider
from cohortsplit.warehouse import PostgresWarehouseAdapter, ReadOnlyExecutor
from cohortsplit.warehouse.errors import WarehouseNotConfiguredError
from tests.auth.conftest import AuditReader
from tests.auth.helpers import ApiClient, FakeClock, error_code, make_role
from tests.integration.conftest import ScratchWarehouse
from tests.semantic.helpers import SEMANTIC_PERMISSIONS, client_with, use_case_by_template

RUNS = "/api/crawler/runs"
ENTRIES = "/api/semantic/business-context"


def environment(dsn: str, schemas: tuple[str, ...]) -> CrawlEnvironment:
    return CrawlEnvironment(
        adapter_factory=lambda: PostgresWarehouseAdapter(ReadOnlyExecutor(SecretStr(dsn))),
        crawler_settings=lambda: CrawlerSettings(schemas=schemas),
    )


@pytest.fixture
def scratch_env(app: FastAPI, scratch: ScratchWarehouse, warehouse_ro_dsn: str) -> CrawlEnvironment:
    env = environment(warehouse_ro_dsn, (scratch.schema,))
    app.dependency_overrides[get_crawl_environment] = lambda: env
    return env


@pytest.fixture
def analyst(app: FastAPI, db: Session, clock: FakeClock) -> ApiClient:
    return client_with(app, db, clock, SEMANTIC_PERMISSIONS)


def crawl(client: ApiClient) -> dict[str, Any]:
    response = client.post(RUNS)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def version(client: ApiClient) -> str:
    value: str = client.get("/api/semantic/version").json()["version"]
    return value


def run_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return len(conn.execute(select(crawl_runs.c.id)).all())


def purchase(schema: str) -> dict[str, Any]:
    return {
        "key": "purchase",
        "synonyms": ["bought"],
        "description": "Paid or delivered orders",
        "definition": {
            "kind": "metric",
            "table": f"{schema}.orders",
            "filters": [
                {
                    "column": f"{schema}.orders.status",
                    "operator": "in",
                    "value": ["paid", "delivered"],
                }
            ],
            "time_column": f"{schema}.orders.ordered_at",
        },
    }


@pytest.fixture
def hold_crawl_lock(engine: Engine) -> Iterator[None]:
    """Another process (API worker or CLI) is crawling: it holds the crawl lock."""
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": CRAWL_RUN_LOCK_KEY})
        conn.commit()
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": CRAWL_RUN_LOCK_KEY})
            conn.commit()


def test_crawl_via_api_stores_docs_and_reports_the_run(
    analyst: ApiClient,
    engine: Engine,
    scratch: ScratchWarehouse,
    scratch_env: CrawlEnvironment,
    audit_events: AuditReader,
) -> None:
    body = crawl(analyst)

    assert body["status"] == "succeeded"
    assert len(body["content_hash"]) == 64
    assert body["summary"]["tables"] == 5
    assert body["triggered_by"].startswith("user:")
    assert body["semantic_version"] == version(analyst)
    docs = analyst.get("/api/semantic/docs").json()
    assert {t["qualified_name"] for t in docs["tables"]} == {
        f"{scratch.schema}.{t}" for t in ("users", "products", "orders", "order_items", "carts")
    }
    listed = analyst.get(RUNS).json()["items"]
    assert [r["id"] for r in listed] == [body["id"]]
    start = audit_events.of("crawler.start")
    run = audit_events.of("crawler.run")
    assert [(e.outcome, e.actor_type) for e in start] == [("success", "user")]
    assert [(e.outcome, e.actor_type, e.target_id) for e in run] == [
        ("success", "user", str(body["id"]))
    ]


def test_crawl_records_a_semantic_version_with_actor(
    analyst: ApiClient, scratch_env: CrawlEnvironment
) -> None:
    body = crawl(analyst)

    [latest, *_] = analyst.get("/api/semantic/versions").json()["items"]
    assert latest["cause"] == "crawler.run"
    assert latest["target"] == f"crawl_run:{body['id']}"
    assert latest["actor"]["display_name"] == "Analyst"
    assert latest["version"] == body["semantic_version"]


def test_recrawl_via_http_preserves_business_context_and_confirmed_use_cases(
    analyst: ApiClient, engine: Engine, scratch: ScratchWarehouse, scratch_env: CrawlEnvironment
) -> None:
    crawl(analyst)
    assert analyst.post(ENTRIES, json=purchase(scratch.schema)).status_code == 201
    confirmed = use_case_by_template(engine, "orders_with_status")
    rejected = use_case_by_template(engine, "spent_more_than")
    edited = use_case_by_template(engine, "cart_with_status")
    assert analyst.post(f"/api/semantic/use-cases/{confirmed.id}/confirm").status_code == 200
    assert analyst.post(f"/api/semantic/use-cases/{rejected.id}/reject").status_code == 200
    edit = {"nl_request": "Users with an abandoned cart (edited)", "spec": edited.spec}
    assert analyst.put(f"/api/semantic/use-cases/{edited.id}", json=edit).status_code == 200
    entries_before = analyst.get(ENTRIES).json()
    queue_before = {
        u["id"]: u
        for u in analyst.get("/api/semantic/use-cases").json()["items"]
        if u["status"] != "pending_review"
    }
    version_before = version(analyst)
    history_before = analyst.get("/api/semantic/versions").json()["total"]

    crawl(analyst)

    assert analyst.get(ENTRIES).json() == entries_before
    queue_after = {u["id"]: u for u in analyst.get("/api/semantic/use-cases").json()["items"]}
    for use_case_id, before in queue_before.items():
        assert queue_after[use_case_id] == before
    templates = [u["template_key"] for u in queue_after.values()]
    assert len(templates) == len(set(templates)), "reviewed or edited templates not regenerated"
    # Unchanged warehouse: same content, same version, no new history row.
    assert version(analyst) == version_before
    assert analyst.get("/api/semantic/versions").json()["total"] == history_before


def test_http_recrawl_flags_confirmed_use_case_whose_column_was_removed(
    app: FastAPI,
    analyst: ApiClient,
    engine: Engine,
    scratch: ScratchWarehouse,
    scratch_env: CrawlEnvironment,
) -> None:
    crawl(analyst)
    target = use_case_by_template(engine, "orders_with_status")
    assert "status" in str(target.referenced_columns)
    assert analyst.post(f"/api/semantic/use-cases/{target.id}/confirm").status_code == 200
    assert analyst.post(ENTRIES, json=purchase(scratch.schema)).status_code == 201
    version_before = version(analyst)

    scratch.admin("ALTER TABLE {s}.orders DROP COLUMN status")
    crawl(analyst)

    [flagged] = [
        u for u in analyst.get("/api/semantic/use-cases").json()["items"] if u["id"] == target.id
    ]
    assert flagged["status"] == "needs_rereview"
    assert f"{scratch.schema}.orders.status" in flagged["review_note"]
    assert flagged["spec"] == target.spec  # never silently changed
    provider = SemanticContextProvider(
        sessionmaker(engine), crawler_settings=CrawlerSettings(), export_grants=NoExportGrants()
    )
    snapshot = provider.snapshot()
    assert target.id not in {u.id for u in snapshot.confirmed_use_cases}
    assert snapshot.stale_business_context == ("purchase",)
    [entry] = analyst.get(ENTRIES).json()["items"]
    assert {"reference": f"{scratch.schema}.orders.status", "problem": "unknown column"} in entry[
        "missing_references"
    ]
    assert version(analyst) != version_before


def test_second_concurrent_crawl_refused_with_409(
    analyst: ApiClient,
    engine: Engine,
    scratch_env: CrawlEnvironment,
    hold_crawl_lock: None,
    audit_events: AuditReader,
) -> None:
    response = analyst.post(RUNS)

    assert response.status_code == 409
    assert error_code(response) == "crawl_in_progress"
    assert run_count(engine) == 0
    [denied] = audit_events.of("crawler.start")
    assert denied.outcome == "denied"
    assert audit_events.of("crawler.run") == []


def test_lock_is_released_after_each_crawl(
    analyst: ApiClient, engine: Engine, scratch_env: CrawlEnvironment
) -> None:
    first = crawl(analyst)
    second = crawl(analyst)

    assert second["id"] > first["id"]
    assert [r["id"] for r in analyst.get(RUNS).json()["items"]] == [second["id"], first["id"]]
    assert analyst.get(RUNS, params={"limit": 1}).json()["items"][0]["id"] == second["id"]


def test_failed_crawl_returns_502_and_keeps_previous_content(
    app: FastAPI,
    analyst: ApiClient,
    engine: Engine,
    scratch_env: CrawlEnvironment,
    warehouse_ro_dsn: str,
    audit_events: AuditReader,
) -> None:
    crawl(analyst)
    docs_before = analyst.get("/api/semantic/docs").json()["tables"]
    app.dependency_overrides[get_crawl_environment] = lambda: environment(
        warehouse_ro_dsn, ("cs_schema_that_does_not_exist",)
    )

    response = analyst.post(RUNS)

    assert response.status_code == 502
    assert error_code(response) == "crawl_failed"
    run_id = response.json()["error"]["run_id"]
    [failed] = [r for r in analyst.get(RUNS).json()["items"] if r["id"] == run_id]
    assert failed["status"] == "failed"
    assert analyst.get("/api/semantic/docs").json()["tables"] == docs_before
    assert [e.outcome for e in audit_events.of("crawler.run")] == ["success", "error"]


def test_unconfigured_warehouse_is_503(app: FastAPI, analyst: ApiClient, engine: Engine) -> None:
    def not_configured() -> PostgresWarehouseAdapter:
        raise WarehouseNotConfiguredError("COHORTSPLIT_WAREHOUSE_DSN is not set.")

    app.dependency_overrides[get_crawl_environment] = lambda: CrawlEnvironment(
        adapter_factory=not_configured, crawler_settings=CrawlerSettings
    )

    response = analyst.post(RUNS)

    assert response.status_code == 503
    assert error_code(response) == "warehouse_not_configured"
    assert run_count(engine) == 0


def test_crawl_start_fails_closed_without_audit(
    analyst: ApiClient, engine: Engine, scratch_env: CrawlEnvironment, audit_store_down: None
) -> None:
    response = analyst.post(RUNS)

    assert response.status_code == 503
    assert error_code(response) == "audit_unavailable"
    assert run_count(engine) == 0


def test_crawl_uses_role_export_grants(
    analyst: ApiClient,
    db: Session,
    clock: FakeClock,
    scratch: ScratchWarehouse,
    scratch_env: CrawlEnvironment,
) -> None:
    make_role(db, "Contact", ["cohort.export"], [f"{scratch.schema}.carts.status"], now=clock.now)

    crawl(analyst)

    tables = {t["qualified_name"]: t for t in analyst.get("/api/semantic/docs").json()["tables"]}
    carts = tables[f"{scratch.schema}.carts"]
    orders = tables[f"{scratch.schema}.orders"]
    assert next(c for c in carts["columns"] if c["name"] == "status")["sample_values"] == []
    assert next(c for c in orders["columns"] if c["name"] == "status")["sample_values"]


def test_crawl_version_is_recorded_atomically_with_the_swap(
    analyst: ApiClient,
    engine: Engine,
    scratch: ScratchWarehouse,
    scratch_env: CrawlEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review fix (S3): the crawl's content and its history row commit together, so the
    history can neither miss a crawl nor credit its change to a later edit."""
    crawl(analyst)
    docs_before = analyst.get("/api/semantic/docs").json()["tables"]
    version_before = version(analyst)
    history_before = analyst.get("/api/semantic/versions").json()["total"]
    scratch.admin("ALTER TABLE {s}.orders DROP COLUMN status")

    def history_down(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("version history unavailable")

    monkeypatch.setattr(semantic_repo, "record_version_if_changed", history_down)
    response = analyst.post(RUNS)

    assert response.status_code == 502, response.text
    assert error_code(response) == "crawl_failed"
    assert analyst.get("/api/semantic/docs").json()["tables"] == docs_before
    assert version(analyst) == version_before
    assert analyst.get("/api/semantic/versions").json()["total"] == history_before

    monkeypatch.undo()
    body = crawl(analyst)  # the lock was released after the failure

    [latest, *_] = analyst.get("/api/semantic/versions").json()["items"]
    assert (latest["cause"], latest["target"]) == ("crawler.run", f"crawl_run:{body['id']}")
    assert latest["version"] == body["semantic_version"] == version(analyst) != version_before


def test_lock_is_released_after_a_failed_crawl(
    app: FastAPI,
    analyst: ApiClient,
    scratch_env: CrawlEnvironment,
    warehouse_ro_dsn: str,
) -> None:
    app.dependency_overrides[get_crawl_environment] = lambda: environment(
        warehouse_ro_dsn, ("cs_schema_that_does_not_exist",)
    )
    assert analyst.post(RUNS).status_code == 502

    app.dependency_overrides[get_crawl_environment] = lambda: scratch_env

    assert crawl(analyst)["status"] == "succeeded"
