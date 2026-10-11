"""POST /api/cohorts/interpret: interpretation | clarification | rejection, nothing executed
(FR-4, FR-5, BR-2, BR-3, BR-4, BR-6, AC-6..AC-9, AC-28)."""

import logging
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cohortsplit.cohort.models import CohortInterpretation
from cohortsplit.cohort_spec.v1 import CohortSpecV1, spec_hash
from cohortsplit.config import ConfigError
from cohortsplit.llm.client import LlmNotConfiguredError, LlmUnavailableError
from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.cohort.helpers import (
    AC2_READABLE,
    AC2_REQUEST,
    AC2_SPEC,
    INTERPRET,
    install_environment,
    marketer,
)
from tests.constants import APPDB_PASSWORD, WAREHOUSE_PASSWORD
from tests.llm_fakes import clarification, interpretation, rejection
from tests.unit.compiler.fixtures import DEFAULT_ENTRIES, ORDERS_HOP, shop_snapshot


@pytest.fixture
def settings_overrides() -> dict[str, object]:
    """A configured warehouse whose password must never reach the prompt or the logs."""
    return {
        "warehouse_dsn": SecretStr(
            f"postgresql://cohortsplit_ro:{WAREHOUSE_PASSWORD}@127.0.0.1:1/ecommerce"
        )
    }


@pytest.fixture
def client(app: FastAPI, db: Session, clock: FakeClock) -> ApiClient:
    return marketer(app, db, clock)


def interpret(client: ApiClient, request: str) -> dict[str, Any]:
    response = client.post(INTERPRET, json={"nl_request": request})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def stored_interpretations(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(CohortInterpretation)) or 0)


def test_interpretation_with_readable_conditions_spec_hash_and_version(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    env = install_environment(app)

    body = interpret(client, AC2_REQUEST)

    assert body["kind"] == "interpretation"
    assert body["readable"] == AC2_READABLE
    spec = CohortSpecV1.model_validate(AC2_SPEC)
    assert CohortSpecV1.model_validate(body["spec"]) == spec
    assert body["spec_hash"] == spec_hash(spec)
    assert body["semantic_version"] == env.snapshot().semantic_version
    assert len(body["interpretation_id"]) == 32
    assert body["expires_at"]
    # FR-5: nothing is executed before confirmation.
    assert env.executor.statements == []
    assert stored_interpretations(db) == 1
    # The request reached the LLM as the last user message of a snapshot-only prompt.
    assert env.llm.calls[0][-1].content == AC2_REQUEST


def test_ac6_best_customers_asks_and_executes_nothing(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    env = install_environment(
        app, replies=[clarification("What makes a customer one of your best customers?")]
    )

    body = interpret(client, "Our best customers")

    assert body["kind"] == "clarification"
    assert "best customers" in body["question"]
    assert env.executor.statements == []
    assert stored_interpretations(db) == 0


def test_ac6_guard_turns_undefined_term_into_clarification(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(
        app,
        replies=[
            interpretation(
                {"spec_version": "1", "where": {"type": "metric", "term": "best customer"}}
            )
        ],
    )

    body = interpret(client, "Our best customers")

    assert body["kind"] == "clarification"
    assert body["unknown_terms"] == ["best customer"]
    assert "best customer" in body["question"]
    assert "Business context" in body["question"]
    assert env.executor.statements == []


def test_ac7_purchased_recently_asks_for_both_terms(app: FastAPI, client: ApiClient) -> None:
    snapshot = shop_snapshot(
        entries=[e for e in DEFAULT_ENTRIES if e.key not in {"purchase", "recently"}]
    )
    env = install_environment(
        app,
        snapshot=snapshot,
        replies=[
            interpretation(
                {
                    "spec_version": "1",
                    "where": {
                        "type": "metric",
                        "term": "purchased",
                        "time_window": {"kind": "term", "term": "recently"},
                    },
                }
            )
        ],
    )

    body = interpret(client, "Users who purchased recently")

    assert body["kind"] == "clarification"
    assert body["unknown_terms"] == ["purchased", "recently"]
    assert "'purchased'" in body["question"] and "'recently'" in body["question"]
    assert env.executor.statements == []


def test_ac8_delete_request_rejected_without_llm_or_warehouse(
    app: FastAPI, client: ApiClient, db: Session
) -> None:
    env = install_environment(app)

    body = interpret(client, "Delete inactive users")

    assert body["kind"] == "rejection"
    assert body["reason"] == "mutation"
    assert env.llm.calls == []
    assert env.executor.statements == []
    assert stored_interpretations(db) == 0


def test_ac8_misbehaving_llm_cannot_smuggle_sql(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(
        app,
        replies=['{"kind": "interpretation", "sql": "DELETE FROM public.users"}'],
    )

    response = client.post(INTERPRET, json={"nl_request": "Users who are inactive"})

    assert response.status_code == 502
    assert error_code(response) == "llm_invalid_output"
    assert len(env.llm.calls) == 3  # bounded retries
    assert env.executor.statements == []


def test_ac9_revenue_question_rejected_as_analytics(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(app)

    body = interpret(client, "What was revenue last month?")

    assert body["kind"] == "rejection"
    assert body["reason"] == "analytics"
    assert "cohort" in body["message"].lower()
    assert env.llm.calls == []


def test_sequences_are_not_supported_yet(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(app)

    body = interpret(client, "Users who added to cart, then didn't buy within 24h")

    assert body["kind"] == "rejection"
    assert body["reason"] == "unsupported_construct"
    assert "not supported yet" in body["message"]
    assert env.llm.calls == []


@pytest.mark.parametrize(
    "reason", ["mutation", "analytics", "unknown_term", "unsupported_construct"]
)
def test_llm_rejections_are_passed_through(app: FastAPI, client: ApiClient, reason: str) -> None:
    install_environment(app, replies=[rejection(reason, "Not a cohort request.")])

    body = interpret(client, "Users who need something special")

    assert (body["kind"], body["reason"], body["message"]) == (
        "rejection",
        reason,
        "Not a cohort request.",
    )


def test_unknown_value_becomes_a_clarification_with_suggestions(
    app: FastAPI, client: ApiClient
) -> None:
    spec = {
        "spec_version": "1",
        "where": {
            "type": "related",
            "table": "public.carts",
            "filters": [{"column": "public.carts.status", "operator": "=", "value": "abandonned"}],
        },
    }
    install_environment(app, replies=[interpretation(spec)])

    body = interpret(client, "Users with an abandonned cart")

    assert body["kind"] == "clarification"
    assert body["unknown_values"] == [
        {"column": "public.carts.status", "value": "abandonned", "suggestions": ["abandoned"]}
    ]


def test_invented_column_is_rejected_naming_it(app: FastAPI, client: ApiClient) -> None:
    spec = {
        "spec_version": "1",
        "where": {
            "type": "related",
            "table": "public.orders",
            "path": [ORDERS_HOP],
            "filters": [{"column": "public.orders.loyalty_tier", "operator": "=", "value": "gold"}],
        },
    }
    env = install_environment(app, replies=[interpretation(spec)])

    body = interpret(client, "Gold loyalty users")

    assert body["kind"] == "rejection"
    assert body["reason"] == "unknown_term"
    assert "public.orders.loyalty_tier" in body["message"]
    assert env.executor.statements == []


def test_canonical_user_id_missing_refuses_before_the_llm(app: FastAPI, client: ApiClient) -> None:
    snapshot = shop_snapshot(entries=[e for e in DEFAULT_ENTRIES if e.key != "customer_id"])
    env = install_environment(app, snapshot=snapshot)

    response = client.post(INTERPRET, json={"nl_request": AC2_REQUEST})

    assert response.status_code == 409
    assert error_code(response) == "canonical_user_id_missing"
    message = response.json()["error"]["message"]
    assert "Define the canonical user identifier in Business context" in message
    assert env.llm.calls == []


def test_no_crawl_refuses_before_the_llm(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(app, snapshot=shop_snapshot(docs=[]))

    response = client.post(INTERPRET, json={"nl_request": AC2_REQUEST})

    assert response.status_code == 409
    assert error_code(response) == "schema_inventory_unavailable"
    assert env.llm.calls == []


def test_llm_not_configured(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(app)
    env.llm_error = LlmNotConfiguredError()

    response = client.post(INTERPRET, json={"nl_request": AC2_REQUEST})

    assert response.status_code == 503
    assert error_code(response) == "llm_not_configured"
    assert "COHORTSPLIT_LLM_BASE_URL" in response.json()["error"]["message"]


def test_llm_misconfigured(app: FastAPI, client: ApiClient) -> None:
    env = install_environment(app)
    env.llm_error = ConfigError("Invalid LLM configuration: COHORTSPLIT_LLM_MAX_ATTEMPTS: bad")

    response = client.post(INTERPRET, json={"nl_request": AC2_REQUEST})

    assert response.status_code == 503
    assert error_code(response) == "llm_misconfigured"


def test_llm_unavailable_is_actionable_and_nothing_is_executed(
    app: FastAPI, client: ApiClient
) -> None:
    env = install_environment(
        app, replies=[LlmUnavailableError("LLM provider unavailable at http://ollama:11434/v1")]
    )

    response = client.post(INTERPRET, json={"nl_request": AC2_REQUEST})

    assert response.status_code == 503
    assert error_code(response) == "llm_unavailable"
    assert "http://ollama:11434/v1" in response.json()["error"]["message"]
    assert env.executor.statements == []


@pytest.mark.parametrize("request_text", ["", "   ", "x" * 1001])
def test_request_text_is_validated(app: FastAPI, client: ApiClient, request_text: str) -> None:
    env = install_environment(app)

    response = client.post(INTERPRET, json={"nl_request": request_text})

    assert response.status_code == 422
    assert env.llm.calls == []


def test_prompt_and_logs_free_of_credentials(
    app: FastAPI, client: ApiClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    env = install_environment(app)

    interpret(client, AC2_REQUEST)

    prompt = env.llm.prompt_text()
    for secret in (WAREHOUSE_PASSWORD, APPDB_PASSWORD, "postgresql://"):
        assert secret not in prompt
        assert secret not in caplog.text
    # Operational logs carry hashes, not the request text (no PII).
    assert AC2_REQUEST not in caplog.text
    assert "spec_hash=" in caplog.text
