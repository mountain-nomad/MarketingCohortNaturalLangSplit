"""Semantic version: changes exactly when content changes (FR-3, AC-25) and keeps an audited
history of who changed what."""

from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.auth.crawler_integration import RoleExportGrants
from cohortsplit.crawler.sampling import NoExportGrants
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.semantic.provider import SemanticContextProvider, current_semantic_version
from tests.auth.helpers import ApiClient, FakeClock, make_role
from tests.semantic.helpers import (
    PURCHASE,
    SEMANTIC_PERMISSIONS,
    client_with,
    first_pending,
    seed_crawl,
)

ENTRIES = "/api/semantic/business-context"


@pytest.fixture
def analyst(app: FastAPI, db: Session, clock: FakeClock) -> ApiClient:
    return client_with(app, db, clock, SEMANTIC_PERMISSIONS)


def version(client: ApiClient) -> str:
    response = client.get("/api/semantic/version")
    assert response.status_code == 200, response.text
    value: str = response.json()["version"]
    return value


def history(client: ApiClient, **query: int) -> dict[str, Any]:
    response = client.get("/api/semantic/versions", params=query)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def without_key(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k != "key"}


def test_version_endpoint_returns_version_and_components(
    analyst: ApiClient, engine: Engine
) -> None:
    seed_crawl(engine)

    body = analyst.get("/api/semantic/version").json()

    assert len(body["version"]) == 64
    assert set(body["components"]) == {
        "business_context",
        "confirmed_use_cases",
        "generated_docs",
    }
    with Session(engine) as db:
        assert current_semantic_version(db).version == body["version"]


def test_version_changes_once_per_content_change_and_is_stable_on_reread(
    analyst: ApiClient, engine: Engine
) -> None:
    seed_crawl(engine)
    before = version(analyst)
    rows_before = history(analyst)["total"]

    assert analyst.post(ENTRIES, json=PURCHASE).status_code == 201

    after = version(analyst)
    assert after != before
    assert version(analyst) == after  # stable on re-read
    assert version(analyst) == after
    assert history(analyst)["total"] == rows_before + 1
    assert history(analyst)["items"][0]["version"] == after


def test_saving_identical_content_keeps_version(analyst: ApiClient, engine: Engine) -> None:
    seed_crawl(engine)
    assert analyst.post(ENTRIES, json=PURCHASE).status_code == 201
    current = version(analyst)
    rows = history(analyst)["total"]

    # Synonyms in another order/case normalize to the same content.
    same = without_key(PURCHASE) | {"synonyms": ["Purchased", " bought "]}
    assert analyst.put(f"{ENTRIES}/purchase", json=same).status_code == 200

    assert version(analyst) == current
    assert history(analyst)["total"] == rows


def test_recrawl_of_unchanged_warehouse_keeps_version(analyst: ApiClient, engine: Engine) -> None:
    seed_crawl(engine)
    current = version(analyst)

    seed_crawl(engine)

    assert version(analyst) == current


def test_pending_use_cases_do_not_affect_version_but_confirmation_does(
    analyst: ApiClient, engine: Engine
) -> None:
    seed_crawl(engine)
    before = version(analyst)
    target = first_pending(engine)

    assert analyst.post(f"/api/semantic/use-cases/{target.id}/confirm").status_code == 200
    confirmed = version(analyst)
    assert confirmed != before

    assert analyst.post(f"/api/semantic/use-cases/{target.id}/reject").status_code == 200
    assert version(analyst) == before  # same content as before the confirmation


def test_history_records_actor_and_cause(analyst: ApiClient, engine: Engine) -> None:
    seed_crawl(engine)
    target = first_pending(engine)
    assert analyst.post(ENTRIES, json=PURCHASE).status_code == 201
    assert analyst.post(f"/api/semantic/use-cases/{target.id}/confirm").status_code == 200
    assert analyst.delete(f"{ENTRIES}/purchase").status_code == 204

    items = history(analyst)["items"]

    assert [(i["cause"], i["target"]) for i in items[:3]] == [
        ("semantic_context.delete", "business_context:purchase"),
        ("use_case.confirm", f"example_use_case:{target.id}"),
        ("semantic_context.create", "business_context:purchase"),
    ]
    assert {i["actor"]["display_name"] for i in items[:3]} == {"Analyst"}
    assert {i["actor_type"] for i in items[:3]} == {"user"}
    assert items[0]["version"] == version(analyst)
    assert set(items[0]["components"]) == {
        "business_context",
        "confirmed_use_cases",
        "generated_docs",
    }


def test_history_is_paginated_newest_first(analyst: ApiClient, engine: Engine) -> None:
    seed_crawl(engine)
    for days in (7, 14, 30):
        entry = {"key": f"window_{days}", "definition": {"kind": "time_window", "last_days": days}}
        assert analyst.post(ENTRIES, json=entry).status_code == 201

    page = history(analyst, limit=2, offset=0)
    rest = history(analyst, limit=2, offset=2)

    assert page["total"] == rest["total"] >= 3
    assert [i["target"] for i in page["items"]] == [
        "business_context:window_30",
        "business_context:window_14",
    ]
    assert rest["items"][0]["target"] == "business_context:window_7"
    ids = [i["id"] for i in page["items"] + rest["items"]]
    assert ids == sorted(ids, reverse=True)


def test_provider_reports_the_same_version(analyst: ApiClient, engine: Engine) -> None:
    seed_crawl(engine)
    assert analyst.post(ENTRIES, json=PURCHASE).status_code == 201
    provider = SemanticContextProvider(
        sessionmaker(engine), crawler_settings=CrawlerSettings(), export_grants=NoExportGrants()
    )

    assert provider.current_semantic_version() == version(analyst)
    assert provider.snapshot().semantic_version == version(analyst)


def test_export_grant_change_changes_the_version_everywhere(
    analyst: ApiClient, engine: Engine, db: Session, clock: FakeClock
) -> None:
    """Review fix: the version identifies the context the LLM sees, including which samples
    today's policy exposes (a newly export-granted column's samples disappear)."""
    seed_crawl(engine)
    before = version(analyst)

    make_role(db, "Contact", ["cohort.export"], ["public.orders.status"], now=clock.now)

    after = version(analyst)
    assert after != before
    assert version(analyst) == after  # stable on re-read
    with Session(engine) as fresh:
        assert current_semantic_version(fresh).version == after
    provider = SemanticContextProvider(
        sessionmaker(engine),
        crawler_settings=CrawlerSettings(),
        export_grants=RoleExportGrants(sessionmaker(engine)),
    )
    assert provider.snapshot().semantic_version == after
    assert provider.current_semantic_version() == after


def test_recorded_version_matches_reread_for_float_literals(
    analyst: ApiClient, engine: Engine
) -> None:
    """Review fix: the history row is computed from stored (JSONB round-tripped) content."""
    seed_crawl(engine)
    entry = {
        "key": "big_spender",
        "definition": {
            "kind": "metric",
            "table": "public.orders",
            "filters": [{"column": "public.orders.grand_total", "operator": ">", "value": 1e20}],
        },
    }
    assert analyst.post(ENTRIES, json=entry).status_code == 201

    assert history(analyst)["items"][0]["version"] == version(analyst)
