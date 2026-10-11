"""A crawled warehouse with business context, a fake LLM and the real read-only executor."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.auth.crawler_integration import RoleExportGrants
from cohortsplit.cohort.environment import CohortEnvironment, LlmConnection, get_cohort_environment
from cohortsplit.cohort.store import PreparedCohortStore, execute_membership
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.semantic.crawl import CrawlEnvironment, get_crawl_environment
from cohortsplit.semantic.provider import SemanticContextProvider
from cohortsplit.semantic.snapshot import SemanticContextSnapshot
from cohortsplit.warehouse import PostgresWarehouseAdapter, ReadOnlyExecutor
from tests.auth.helpers import ApiClient, FakeClock, make_role, make_user
from tests.cohort.helpers import INTERPRET, PREVIEW, confirm
from tests.llm_fakes import FakeChatClient, interpretation

ENTRIES = "/api/semantic/business-context"


def metric(
    key: str,
    schema: str,
    filters: Sequence[dict[str, Any]],
    *,
    value_column: str | None = None,
    time_column: str | None = None,
    synonyms: Sequence[str] = (),
) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "kind": "metric",
        "table": f"{schema}.orders",
        "filters": list(filters),
    }
    if value_column:
        definition["value_column"] = f"{schema}.orders.{value_column}"
    if time_column:
        definition["time_column"] = f"{schema}.orders.{time_column}"
    return {"key": key, "synonyms": list(synonyms), "description": "", "definition": definition}


def paid_statuses(schema: str) -> dict[str, Any]:
    return {
        "column": f"{schema}.orders.status",
        "operator": "in",
        "value": ["paid", "shipped", "delivered"],
    }


def business_context(schema: str) -> list[dict[str, Any]]:
    """The reviewed business context the AC-1..AC-5 requests need."""
    paid = paid_statuses(schema)
    return [
        {
            "key": "customer_id",
            "description": "Canonical user identifier (BR-1).",
            "definition": {"kind": "canonical_user_id", "column": f"{schema}.users.user_id"},
        },
        metric(
            "purchase",
            schema,
            [paid],
            value_column="grand_total",
            time_column="ordered_at",
            synonyms=("bought",),
        ),
        metric("paid_order", schema, [paid], synonyms=("paid for an order",)),
        metric("spent", schema, [paid], value_column="grand_total", synonyms=("spend",)),
        metric(
            "discounted_order",
            schema,
            [{"column": f"{schema}.orders.discount_total", "operator": ">", "value": 0}],
            synonyms=("used a discount",),
        ),
    ]


@dataclass
class World:
    app: FastAPI
    client: ApiClient
    llm: FakeChatClient
    executor: ReadOnlyExecutor
    snapshot_settings: CrawlerSettings
    session_factory: sessionmaker[Session]

    def snapshot(self) -> SemanticContextSnapshot:
        provider = SemanticContextProvider(
            self.session_factory,
            crawler_settings=self.snapshot_settings,
            export_grants=RoleExportGrants(self.session_factory),
        )
        return provider.snapshot()

    def run(self, request: str, spec: dict[str, Any]) -> dict[str, Any]:
        """Interpret (fake LLM answers ``spec``), confirm, preview; return both bodies."""
        self.llm.replies[:] = [interpretation(spec)]
        interpreted = self.client.post(INTERPRET, json={"nl_request": request})
        assert interpreted.status_code == 200, interpreted.text
        body = interpreted.json()
        assert body["kind"] == "interpretation", body
        previewed = self.client.post(PREVIEW, json=confirm(body))
        assert previewed.status_code == 200, previewed.text
        return {"interpretation": body, "preview": previewed.json()}

    def members(self, preview: dict[str, Any], db: Session, clock: FakeClock) -> set[object]:
        if preview["prepared_cohort_id"] is None:
            return set()
        owner = self.client.get("/api/me").json()["id"]
        prepared = PreparedCohortStore().get(
            db, preview["prepared_cohort_id"], user_id=owner, now=clock.now
        )
        assert prepared is not None
        tables = {t.qualified_name for t in self.snapshot().tables}
        return set(execute_membership(self.executor, prepared, allowed_tables=tables))


def build_world(
    app: FastAPI,
    engine: Engine,
    db: Session,
    clock: FakeClock,
    *,
    ro_dsn: str,
    schema: str,
    executor: ReadOnlyExecutor | None = None,
    entries: Sequence[dict[str, Any]] | None = None,
) -> World:
    settings = CrawlerSettings(schemas=(schema,))
    crawl_env = CrawlEnvironment(
        adapter_factory=lambda: PostgresWarehouseAdapter(ReadOnlyExecutor(SecretStr(ro_dsn))),
        crawler_settings=lambda: settings,
    )
    app.dependency_overrides[get_crawl_environment] = lambda: crawl_env
    role = make_role(
        db,
        "Campaign analyst",
        ["cohort.create", "semantic_context.read", "semantic_context.edit", "crawler.run"],
        now=clock.now,
    )
    make_user(db, "campaign@example.com", now=clock.now, roles=[role])
    client = ApiClient(app)
    assert client.login("campaign@example.com").status_code == 200
    crawled = client.post("/api/crawler/runs")
    assert crawled.status_code == 201, crawled.text
    for entry in business_context(schema) if entries is None else entries:
        created = client.post(ENTRIES, json=entry)
        assert created.status_code == 201, created.text

    world = World(
        app=app,
        client=client,
        llm=FakeChatClient([]),
        executor=executor or ReadOnlyExecutor(SecretStr(ro_dsn)),
        snapshot_settings=settings,
        session_factory=sessionmaker(engine, expire_on_commit=False),
    )
    app.dependency_overrides[get_cohort_environment] = lambda: CohortEnvironment(
        snapshot=world.snapshot,
        llm=lambda: LlmConnection(world.llm, 3),
        executor=lambda: world.executor,
    )
    return world


def reference(dsn: str, sql: str, params: dict[str, Any] | None = None) -> set[object]:
    """Run hand-written reference SQL (read-only role) and return the user id set."""
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        return {row[0] for row in conn.execute(sql, params).fetchall()}  # type: ignore[arg-type]
