"""The prompt contains only the semantic snapshot (BR-7, AC-24, AC-28)."""

import inspect
import json
from typing import Any

from cohortsplit.llm.prompt import build_prompt
from cohortsplit.semantic.snapshot import UseCaseRow, assemble_snapshot
from tests.constants import APPDB_PASSWORD, WAREHOUSE_PASSWORD
from tests.unit.compiler.fixtures import DEFAULT_ENTRIES, OPEN_POLICY, docs_for

CONFIRMED_V1 = {
    "spec_version": "1",
    "entity": "user",
    "where": {
        "type": "related",
        "table": "public.carts",
        "path": [{"from_column": "public.users.user_id", "to_column": "public.carts.user_id"}],
        "filters": [{"column": "public.carts.status", "operator": "=", "value": "abandoned"}],
    },
}
CONFIRMED_DRAFT = {
    "spec_version": "draft-0",
    "entity": {"table": "public.users", "key": "user_id"},
    "where": {
        "type": "attribute",
        "filter": {"column": "public.users.is_active", "operator": "=", "value": True},
    },
}
PENDING = {**CONFIRMED_V1, "where": {**CONFIRMED_V1["where"], "table": "public.carts"}}


def snapshot() -> Any:
    return assemble_snapshot(
        entries=list(DEFAULT_ENTRIES),
        use_cases=[
            UseCaseRow(1, "confirmed", "Users with an abandoned cart", CONFIRMED_V1, "1", "human"),
            UseCaseRow(2, "confirmed", "Active users", CONFIRMED_DRAFT, "draft-0", "human"),
            UseCaseRow(3, "pending_review", "PENDING-USE-CASE-TEXT", PENDING, "1", "generated"),
            UseCaseRow(4, "rejected", "REJECTED-USE-CASE-TEXT", PENDING, "1", "human"),
        ],
        generated_docs=docs_for(),
        policy=OPEN_POLICY,
    )


def text(messages: Any) -> str:
    return "\n".join(m.content for m in messages)


def test_prompt_is_built_only_from_the_snapshot_and_the_request() -> None:
    assert list(inspect.signature(build_prompt).parameters) == ["snapshot", "nl_request"]


def test_prompt_contains_schema_business_context_samples_and_request() -> None:
    messages = build_prompt(snapshot(), "Users from Kazakhstan")
    content = text(messages)

    assert messages[0].role == "system"
    assert messages[-1].role == "user"
    assert messages[-1].content.endswith("Users from Kazakhstan")
    for expected in (
        "public.orders",
        "ordered_at",
        "public.addresses.country_code",  # FK/columns appear qualified
        "KZ",  # policy-permitted sample value
        "purchase",  # business context key
        "paid",  # its definition
        "public.users.user_id",  # canonical user id
        '"spec_version": "1"',  # the output contract
        "clarification",
        "rejection",
        "unsupported_construct",
    ):
        assert expected in content, expected


def test_confirmed_use_cases_are_few_shot_pairs_upgraded_to_v1() -> None:
    messages = build_prompt(snapshot(), "x")
    pairs = [
        (messages[i].content, messages[i + 1].content)
        for i in range(1, len(messages) - 1)
        if messages[i].role == "user" and messages[i + 1].role == "assistant"
    ]

    examples = {request: json.loads(answer) for request, answer in pairs}
    assert examples["Users with an abandoned cart"] == {
        "kind": "interpretation",
        "spec": CONFIRMED_V1,
    }
    assert examples["Active users"]["spec"]["spec_version"] == "1"
    assert examples["Active users"]["spec"]["where"]["type"] == "attribute"


def test_prompt_never_contains_credentials_rows_or_pending_use_cases() -> None:
    content = text(build_prompt(snapshot(), "Users from DE"))

    for forbidden in (
        WAREHOUSE_PASSWORD,
        APPDB_PASSWORD,
        "postgresql://",
        "PENDING-USE-CASE-TEXT",
        "REJECTED-USE-CASE-TEXT",
        "password_hash",  # denylisted columns appear as schema only, never with values
    ):
        if forbidden == "password_hash":
            assert '"x"' not in content  # its fake value
            continue
        assert forbidden not in content, forbidden


def test_denylisted_columns_have_no_sample_values() -> None:
    messages = build_prompt(snapshot(), "x")
    content = text(messages)
    # Values appear in the JSON context as quoted strings; column names may contain them.
    for value in ("a@example.com", "+100", "tok", "1 Main St"):
        assert json.dumps(value) not in content, value
    context = json.loads(messages[0].content.split("Context (JSON):\n", 1)[1])
    columns = {c["column"]: c for t in context["tables"] for c in t["columns"]}
    for denylisted in (
        "public.users.email",
        "public.users.phone",
        "public.users.password_hash",
        "public.carts.session_token",
        "public.addresses.line1",
    ):
        assert "sample_values" not in columns[denylisted], denylisted


def test_prompt_is_deterministic() -> None:
    assert build_prompt(snapshot(), "Users from DE") == build_prompt(snapshot(), "Users from DE")
