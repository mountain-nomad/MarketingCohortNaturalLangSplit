"""SemanticContextProvider: the LLM context contains only BR-7 sources (AC-24)."""

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.auth.crawler_integration import RoleExportGrants
from cohortsplit.crawler.sampling import NoExportGrants
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.semantic.provider import SemanticContextProvider
from tests.auth.helpers import ApiClient, FakeClock, make_role
from tests.semantic.helpers import (
    CANONICAL_USER,
    PURCHASE,
    SEMANTIC_PERMISSIONS,
    client_with,
    first_pending,
    seed_crawl,
    set_status,
    use_case_by_template,
    use_cases,
)


@pytest.fixture
def analyst(app: FastAPI, db: Session, clock: FakeClock) -> ApiClient:
    return client_with(app, db, clock, SEMANTIC_PERMISSIONS)


@pytest.fixture
def provider(engine: Engine) -> SemanticContextProvider:
    return SemanticContextProvider(
        sessionmaker(engine), crawler_settings=CrawlerSettings(), export_grants=NoExportGrants()
    )


def test_pending_use_cases_excluded_then_included_after_confirm(
    analyst: ApiClient, engine: Engine, provider: SemanticContextProvider
) -> None:
    seed_crawl(engine)
    pending = use_cases(engine, status="pending_review")
    assert pending  # the crawler produced reviewable suggestions
    assert provider.confirmed_use_cases_for_prompt() == ()

    target = pending[0]
    assert analyst.post(f"/api/semantic/use-cases/{target.id}/confirm").status_code == 200

    prompt = provider.confirmed_use_cases_for_prompt()
    assert [(u.id, u.nl_request) for u in prompt] == [(target.id, target.nl_request)]
    assert prompt[0].spec == target.spec


def test_rejected_and_needs_rereview_never_in_prompt(
    engine: Engine, provider: SemanticContextProvider
) -> None:
    seed_crawl(engine)
    first, second, third = use_cases(engine)[:3]
    set_status(engine, first.id, "rejected")
    set_status(engine, second.id, "needs_rereview", "column gone")
    set_status(engine, third.id, "confirmed")

    assert [u.id for u in provider.confirmed_use_cases_for_prompt()] == [third.id]


def test_snapshot_contains_only_br7_sources(
    analyst: ApiClient, engine: Engine, provider: SemanticContextProvider
) -> None:
    seed_crawl(engine)
    assert analyst.post("/api/semantic/business-context", json=PURCHASE).status_code == 201
    assert analyst.post("/api/semantic/business-context", json=CANONICAL_USER).status_code == 201
    target = first_pending(engine)
    assert analyst.post(f"/api/semantic/use-cases/{target.id}/confirm").status_code == 200

    snapshot = provider.snapshot()

    assert [e.key for e in snapshot.business_context] == ["customer_id", "purchase"]
    assert snapshot.canonical_user_id == "public.users.user_id"
    assert [u.id for u in snapshot.confirmed_use_cases] == [target.id]
    tables = {t.qualified_name: t for t in snapshot.tables}
    assert "public.orders" in tables
    status = next(c for c in tables["public.orders"].columns if c.name == "status")
    assert "delivered" in status.sample_values
    email = next(c for c in tables["public.users"].columns if c.name == "email")
    assert email.sample_values == ()
    # Pending generated text never appears anywhere in the snapshot.
    pending_texts = {u.nl_request for u in use_cases(engine, status="pending_review")}
    assert not pending_texts & {u.nl_request for u in snapshot.confirmed_use_cases}


def test_canonical_user_id_unset_until_configured(
    analyst: ApiClient, engine: Engine, provider: SemanticContextProvider
) -> None:
    seed_crawl(engine)
    assert provider.canonical_user_id() is None

    assert analyst.post("/api/semantic/business-context", json=CANONICAL_USER).status_code == 201

    assert provider.canonical_user_id() == "public.users.user_id"


def test_samples_of_newly_export_granted_columns_are_withheld(
    engine: Engine, db: Session, clock: FakeClock
) -> None:
    seed_crawl(engine)
    make_role(db, "Contact", ["cohort.export"], ["public.orders.status"], now=clock.now)
    provider = SemanticContextProvider(
        sessionmaker(engine),
        crawler_settings=CrawlerSettings(),
        export_grants=RoleExportGrants(sessionmaker(engine)),
    )

    orders = next(t for t in provider.snapshot().tables if t.qualified_name == "public.orders")

    status = next(c for c in orders.columns if c.name == "status")
    assert status.sample_values == ()


def test_sampling_disabled_withholds_every_sample(engine: Engine) -> None:
    seed_crawl(engine)
    provider = SemanticContextProvider(
        sessionmaker(engine),
        crawler_settings=CrawlerSettings(sampling_enabled=False),
        export_grants=NoExportGrants(),
    )

    assert all(not c.sample_values for t in provider.snapshot().tables for c in t.columns)


def test_docs_endpoint_shows_generated_docs_with_permitted_samples(
    app: FastAPI, db: Session, clock: FakeClock, engine: Engine
) -> None:
    seed_crawl(engine)
    reader = client_with(app, db, clock, {"semantic_context.read"}, email="reader@example.com")

    response = reader.get("/api/semantic/docs")

    assert response.status_code == 200, response.text
    body = response.json()
    tables = {t["qualified_name"]: t for t in body["tables"]}
    orders = tables["public.orders"]
    assert orders["primary_key"] == ["order_id"]
    assert orders["estimated_row_count"] == 830
    assert orders["foreign_keys"] == [
        {"columns": ["user_id"], "referred_table": "public.users", "referred_columns": ["user_id"]}
    ]
    status = next(c for c in orders["columns"] if c["name"] == "status")
    assert "delivered" in status["sample_values"]
    phone = next(c for c in tables["public.users"]["columns"] if c["name"] == "phone")
    assert phone["sample_values"] == []
    assert body["latest_run"]["status"] == "succeeded"


def test_docs_endpoint_before_any_crawl(analyst: ApiClient) -> None:
    body = analyst.get("/api/semantic/docs").json()

    assert body == {"tables": [], "latest_run": None}


def test_confirmed_generated_use_case_withheld_once_its_literal_column_is_export_granted(
    analyst: ApiClient, engine: Engine, db: Session, clock: FakeClock
) -> None:
    """Review fix (BR-7 / S7): a sampled literal copied into a generated use case must not
    reach the prompt once today's policy forbids that column's values."""
    seed_crawl(engine)
    target = use_case_by_template(engine, "from_country")
    assert analyst.post(f"/api/semantic/use-cases/{target.id}/confirm").status_code == 200
    provider = SemanticContextProvider(
        sessionmaker(engine),
        crawler_settings=CrawlerSettings(),
        export_grants=RoleExportGrants(sessionmaker(engine)),
    )
    assert target.id in {u.id for u in provider.snapshot().confirmed_use_cases}

    make_role(db, "Contact", ["cohort.export"], ["public.addresses.country_code"], now=clock.now)
    snapshot = provider.snapshot()

    assert target.id not in {u.id for u in snapshot.confirmed_use_cases}
    assert target.id in snapshot.withheld_use_cases
    assert target.nl_request not in {u.nl_request for u in snapshot.confirmed_use_cases}
