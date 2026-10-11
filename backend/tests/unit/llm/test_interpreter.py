"""Tagged LLM results, bounded retries on schema violations, raw output logged (FR-4)."""

import json
import logging

import pytest

from cohortsplit.cohort_spec.v1 import CohortSpecV1
from cohortsplit.llm.client import ChatMessage, LlmUnavailableError
from cohortsplit.llm.interpreter import (
    Clarification,
    Interpretation,
    LlmInvalidOutputError,
    Rejection,
    interpret_request,
)
from tests.llm_fakes import FakeChatClient, clarification, interpretation, rejection

PROMPT = [ChatMessage("system", "rules"), ChatMessage("user", "Users from DE")]
SPEC = {
    "spec_version": "1",
    "where": {
        "type": "attribute",
        "filter": {"column": "public.users.is_active", "operator": "=", "value": True},
    },
}


def test_interpretation_is_a_typed_spec() -> None:
    result = interpret_request(FakeChatClient([interpretation(SPEC)]), PROMPT, max_attempts=3)

    assert isinstance(result, Interpretation)
    assert result.spec == CohortSpecV1.model_validate(SPEC)


def test_clarification_and_rejections() -> None:
    asked = interpret_request(
        FakeChatClient([clarification("Paid or delivered?")]), PROMPT, max_attempts=1
    )
    assert asked == Clarification(question="Paid or delivered?")

    for reason in ("mutation", "analytics", "unknown_term", "unsupported_construct"):
        result = interpret_request(
            FakeChatClient([rejection(reason, "no")]), PROMPT, max_attempts=1
        )
        assert isinstance(result, Rejection)
        assert result.reason == reason


def test_json_in_a_code_fence_is_accepted() -> None:
    fenced = "```json\n" + clarification("Which country?") + "\n```"
    assert interpret_request(FakeChatClient([fenced]), PROMPT, max_attempts=1) == Clarification(
        question="Which country?"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        json.dumps(
            {"kind": "interpretation", "spec": {"spec_version": "1", "where": {"type": "sequence"}}}
        ),
        json.dumps({"kind": "interpretation", "sql": "DELETE FROM users"}),
        json.dumps({"kind": "rejection", "reason": "because"}),
        json.dumps({"kind": "answer", "text": "42"}),
        json.dumps([1, 2]),
    ],
    ids=["not-json", "sequence-node", "sql", "unknown-reason", "unknown-kind", "array"],
)
def test_schema_violation_is_retried_with_the_error_fed_back(bad: str) -> None:
    client = FakeChatClient([bad, clarification("ok?")])

    result = interpret_request(client, PROMPT, max_attempts=3)

    assert result == Clarification(question="ok?")
    assert len(client.calls) == 2
    retry = client.calls[1]
    assert retry[: len(PROMPT)] == PROMPT
    assert retry[-2] == ChatMessage("assistant", bad)
    assert retry[-1].role == "user"
    assert "invalid" in retry[-1].content.lower()


def test_bounded_retries_then_explicit_error_with_raw_output_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    client = FakeChatClient(["garbage-output-1"])

    with pytest.raises(LlmInvalidOutputError) as info:
        interpret_request(client, PROMPT, max_attempts=3)

    assert len(client.calls) == 3
    assert info.value.attempts == 3
    assert "3" in str(info.value)
    assert "garbage-output-1" in caplog.text


def test_raw_output_is_logged_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    leaky = "postgresql://cohortsplit_ro:hunter2-secret@db:5432/x Bearer sk-abc123secret"

    with pytest.raises(LlmInvalidOutputError):
        interpret_request(FakeChatClient([leaky]), PROMPT, max_attempts=1)

    assert "hunter2-secret" not in caplog.text
    assert "sk-abc123secret" not in caplog.text


def test_unavailable_provider_is_not_retried() -> None:
    client = FakeChatClient([LlmUnavailableError("LLM provider unavailable at http://x/v1")])

    with pytest.raises(LlmUnavailableError):
        interpret_request(client, PROMPT, max_attempts=3)

    assert len(client.calls) == 1
