"""LLM context snapshot: only BR-7 sources; samples re-checked with the current policy (AC-24)."""

import dataclasses
from typing import Any

import pytest

from cohortsplit.crawler.metadata import collect_catalog
from cohortsplit.crawler.sampling import DEFAULT_SAMPLE_DENYLIST, SamplingPolicy
from cohortsplit.crawler.store import table_doc_key
from cohortsplit.semantic.snapshot import (
    PromptBusinessContext,
    PromptUseCase,
    UseCaseRow,
    assemble_snapshot,
)
from cohortsplit.semantic.version import (
    DocContent,
    EntryContent,
    UseCaseContent,
    compute_semantic_version,
)
from tests.fakes import FakeAdapter, shop_tables

OPEN_POLICY = SamplingPolicy(enabled=True, denylist=DEFAULT_SAMPLE_DENYLIST)


def generated_docs() -> list[DocContent]:
    catalog = collect_catalog(FakeAdapter(shop_tables()), OPEN_POLICY)
    docs = [
        DocContent(table_doc_key(t.qualified_name), "table_schema", t.model_dump(mode="json"))
        for t in catalog.tables
    ]
    docs += [
        DocContent(table_doc_key(p.table), "data_profile", p.model_dump(mode="json"))
        for p in catalog.profiles
    ]
    return docs


DOCS = generated_docs()


def use_case(id_: int, status: str, text: str | None = None) -> UseCaseRow:
    return UseCaseRow(
        id=id_,
        status=status,
        nl_request=text or f"use case {id_}",
        spec={"spec_version": "draft-0", "n": id_},
        spec_version="draft-0",
    )


ALL_STATUSES = [
    use_case(1, "pending_review"),
    use_case(2, "confirmed"),
    use_case(3, "rejected"),
    use_case(4, "needs_rereview"),
    use_case(5, "confirmed"),
]

CANONICAL = EntryContent(
    key="customer_id",
    kind="canonical_user_id",
    synonyms=(),
    description="",
    definition={"kind": "canonical_user_id", "column": "public.users.user_id"},
)
PURCHASE = EntryContent(
    key="purchase",
    kind="metric",
    synonyms=("bought",),
    description="Delivered orders",
    definition={
        "kind": "metric",
        "table": "public.orders",
        "filters": [{"column": "public.orders.status", "operator": "=", "value": "delivered"}],
    },
)
STALE = EntryContent(
    key="liked",
    kind="term",
    synonyms=("favourite",),
    description="",
    definition={"kind": "term", "table": "public.likes"},
)


def snapshot(
    entries: Any = (CANONICAL, PURCHASE),
    use_cases: Any = ALL_STATUSES,
    docs: Any = DOCS,
    policy: SamplingPolicy = OPEN_POLICY,
) -> Any:
    return assemble_snapshot(
        entries=entries, use_cases=use_cases, generated_docs=docs, policy=policy
    )


def test_only_confirmed_use_cases_reach_the_snapshot() -> None:
    result = snapshot()

    assert [u.id for u in result.confirmed_use_cases] == [2, 5]
    assert all(isinstance(u, PromptUseCase) for u in result.confirmed_use_cases)


def test_pending_changes_do_not_change_the_version_but_confirmations_do() -> None:
    base = snapshot().semantic_version
    more_pending = snapshot(use_cases=[*ALL_STATUSES, use_case(6, "pending_review")])
    confirmed = snapshot(use_cases=[*ALL_STATUSES, use_case(6, "confirmed")])

    assert more_pending.semantic_version == base
    assert confirmed.semantic_version != base


def test_version_is_computed_from_the_same_content() -> None:
    expected = compute_semantic_version(
        [CANONICAL, PURCHASE],
        [
            UseCaseContent(u.nl_request, u.spec, u.spec_version)
            for u in ALL_STATUSES
            if u.status == "confirmed"
        ],
        DOCS,
    )

    assert snapshot().semantic_version == expected.version


def test_prompt_shapes_carry_no_review_or_generation_notes() -> None:
    assert {f.name for f in dataclasses.fields(PromptUseCase)} == {
        "id",
        "nl_request",
        "spec",
        "spec_version",
    }
    assert {f.name for f in dataclasses.fields(PromptBusinessContext)} == {
        "key",
        "kind",
        "synonyms",
        "description",
        "definition",
    }


def test_business_context_and_canonical_user_id() -> None:
    result = snapshot()

    assert [e.key for e in result.business_context] == ["customer_id", "purchase"]
    assert result.canonical_user_id == "public.users.user_id"
    assert result.stale_business_context == ()


def test_entries_with_missing_references_are_left_out_and_listed() -> None:
    result = snapshot(entries=[PURCHASE, STALE])

    assert [e.key for e in result.business_context] == ["purchase"]
    assert result.stale_business_context == ("liked",)
    assert result.canonical_user_id is None


def test_canonical_user_id_none_when_column_gone() -> None:
    gone = dataclasses.replace(
        CANONICAL, definition={"kind": "canonical_user_id", "column": "public.users.uid"}
    )

    result = snapshot(entries=[gone])

    assert result.canonical_user_id is None
    assert result.stale_business_context == ("customer_id",)


def _column(result: Any, table: str, column: str) -> Any:
    [match] = [t for t in result.tables if t.qualified_name == table]
    [col] = [c for c in match.columns if c.name == column]
    return col


def test_tables_carry_raw_schema_metadata_and_row_counts() -> None:
    result = snapshot()

    orders = next(t for t in result.tables if t.qualified_name == "public.orders")
    assert orders.primary_key == ("order_id",)
    assert orders.estimated_row_count == 830
    assert [(f.columns, f.referred_table) for f in orders.foreign_keys] == [
        (("user_id",), "public.users")
    ]
    status = _column(result, "public.orders", "status")
    assert status.allowed_values is not None
    assert "delivered" in status.sample_values


@pytest.mark.parametrize(
    "policy",
    [
        SamplingPolicy(enabled=False, denylist=()),
        SamplingPolicy(enabled=True, denylist=("status",)),
        SamplingPolicy(enabled=True, denylist=(), export_granted_columns={"public.orders.status"}),
        SamplingPolicy(enabled=True, denylist=(), export_granted_columns={"orders.status"}),
    ],
    ids=["sampling-disabled", "denylisted-now", "export-granted-now", "export-granted-short"],
)
def test_samples_rechecked_against_current_policy(policy: SamplingPolicy) -> None:
    assert _column(snapshot(), "public.orders", "status").sample_values  # allowed at crawl time

    result = snapshot(policy=policy)

    assert _column(result, "public.orders", "status").sample_values == ()
    # Schema metadata (CHECK constraint values) is not a sample and stays.
    assert _column(result, "public.orders", "status").allowed_values is not None


def test_unsampled_columns_never_expose_values() -> None:
    result = snapshot()

    assert _column(result, "public.users", "email").sample_values == ()
    assert _column(result, "public.users", "phone").sample_values == ()
