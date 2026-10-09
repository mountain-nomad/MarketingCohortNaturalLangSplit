"""Helpers for the semantic-context API tests: seeding crawler output and signed-in clients."""

from collections.abc import Iterable
from typing import Any

from fastapi import FastAPI
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from cohortsplit.crawler.hashing import content_hash
from cohortsplit.crawler.metadata import collect_catalog
from cohortsplit.crawler.sampling import DEFAULT_SAMPLE_DENYLIST, SamplingPolicy
from cohortsplit.crawler.store import CrawlStore, StoredUseCase
from cohortsplit.crawler.tables import example_use_cases
from cohortsplit.crawler.use_cases import generate_use_cases
from tests.auth.helpers import ApiClient, FakeClock, make_role, make_user
from tests.fakes import FakeAdapter, FakeTable, shop_tables

SEMANTIC_PERMISSIONS = (
    "semantic_context.read",
    "semantic_context.edit",
    "use_case.review",
    "crawler.run",
)

PURCHASE: dict[str, Any] = {
    "key": "purchase",
    "synonyms": ["bought", "purchased"],
    "description": "An order the customer paid for.",
    "definition": {
        "kind": "metric",
        "table": "public.orders",
        "filters": [
            {
                "column": "public.orders.status",
                "operator": "in",
                "value": ["paid", "shipped", "delivered"],
            }
        ],
        "value_column": "public.orders.grand_total",
        "time_column": "public.orders.ordered_at",
    },
}

CANONICAL_USER: dict[str, Any] = {
    "key": "customer_id",
    "synonyms": ["user id"],
    "description": "Canonical user identifier (BR-1).",
    "definition": {"kind": "canonical_user_id", "column": "public.users.user_id"},
}


def seed_crawl(engine: Engine, tables: list[FakeTable] | None = None) -> int:
    """Store crawler output for a fake ecommerce warehouse through the real CrawlStore swap."""
    store = CrawlStore(engine)
    policy = SamplingPolicy(enabled=True, denylist=DEFAULT_SAMPLE_DENYLIST)
    catalog = collect_catalog(FakeAdapter(tables or shop_tables()), policy)
    generation = generate_use_cases(catalog)
    run_id = store.start_run("test")
    store.swap_generated_content(
        run_id,
        catalog=catalog,
        generation=generation,
        content_hash=content_hash(catalog, generation),
        summary={},
        scope_schemas=None,
    )
    return run_id


def shop_without(table: str, column: str) -> list[FakeTable]:
    """The fake warehouse with one column removed (a schema change)."""
    tables = shop_tables()
    for fake in tables:
        if fake.ref.name == table:
            fake.columns = [c for c in fake.columns if c.name != column]
    return tables


def use_cases(engine: Engine, **filters: str) -> list[StoredUseCase]:
    return CrawlStore(engine).list_use_cases(**filters)


def use_case_by_template(engine: Engine, template_key: str) -> StoredUseCase:
    [found] = [u for u in use_cases(engine) if u.template_key == template_key]
    return found


def first_pending(engine: Engine) -> StoredUseCase:
    return use_cases(engine, status="pending_review")[0]


def set_status(engine: Engine, use_case_id: int, status: str, note: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(example_use_cases)
            .where(example_use_cases.c.id == use_case_id)
            .values(status=status, review_note=note)
        )


def use_case_status(engine: Engine, use_case_id: int) -> str:
    with engine.connect() as conn:
        status: str = conn.execute(
            select(example_use_cases.c.status).where(example_use_cases.c.id == use_case_id)
        ).scalar_one()
    return status


def client_with(
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    permissions: Iterable[str],
    email: str = "analyst@example.com",
) -> ApiClient:
    role = make_role(db, f"Role for {email}", sorted(set(permissions)), now=clock.now)
    make_user(db, email, now=clock.now, roles=[role], display_name=email.split("@")[0].title())
    client = ApiClient(app)
    assert client.login(email).status_code == 200
    return client


def spec_with_filter(column: str, value: str = "paid") -> dict[str, Any]:
    """A valid draft-0 spec: users with a related order matching ``column = value``."""
    return {
        "spec_version": "draft-0",
        "entity": {"table": "public.users", "key": "user_id"},
        "where": {
            "type": "related",
            "path": [{"from_column": "public.users.user_id", "to_column": "public.orders.user_id"}],
            "filters": [{"column": column, "operator": "=", "value": value}],
        },
    }
