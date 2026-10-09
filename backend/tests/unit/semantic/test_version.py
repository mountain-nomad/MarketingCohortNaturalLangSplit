"""Semantic version: deterministic content hash (FR-3, AC-25, ruling S3)."""

import hashlib
import json
import random
from typing import Any

from cohortsplit.semantic.version import (
    VERSION_FORMAT,
    DocContent,
    EntryContent,
    UseCaseContent,
    compute_semantic_version,
)

ENTRIES = [
    EntryContent(
        key="purchase",
        kind="metric",
        synonyms=("bought",),
        description="Paid orders",
        definition={"kind": "metric", "table": "public.orders", "filters": []},
    ),
    EntryContent(
        key="recently",
        kind="time_window",
        synonyms=(),
        description="",
        definition={"kind": "time_window", "last_days": 30},
    ),
]
USE_CASES = [
    UseCaseContent(nl_request="Users who bought product 7", spec={"a": 1}, spec_version="draft-0"),
    UseCaseContent(nl_request="Users from KZ", spec={"b": [1, 2]}, spec_version="draft-0"),
]
DOCS = [
    DocContent(doc_key="table:public.users", kind="table_schema", content={"name": "users"}),
    DocContent(doc_key="table:public.users", kind="data_profile", content={"rows": 91}),
    DocContent(doc_key="table:public.orders", kind="table_schema", content={"name": "orders"}),
]


def version(entries: Any = ENTRIES, use_cases: Any = USE_CASES, docs: Any = DOCS) -> Any:
    return compute_semantic_version(entries, use_cases, docs)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def test_version_is_sha256_hex_and_deterministic() -> None:
    first = version()

    assert len(first.version) == 64
    int(first.version, 16)
    assert version() == first


def test_version_ignores_input_order() -> None:
    shuffled = random.Random(7)  # noqa: S311 (test data order, not security)
    entries, use_cases, docs = list(ENTRIES), list(USE_CASES), list(DOCS)
    for items in (entries, use_cases, docs):
        shuffled.shuffle(items)
        items.reverse()

    assert version(entries, use_cases, docs) == version()


def test_version_matches_the_documented_formula() -> None:
    business = _sha(
        sorted(
            (
                {
                    "key": e.key,
                    "kind": e.kind,
                    "synonyms": list(e.synonyms),
                    "description": e.description,
                    "definition": e.definition,
                }
                for e in ENTRIES
            ),
            key=lambda item: item["key"],
        )
    )
    use_cases = _sha(
        sorted(
            (
                {"nl_request": u.nl_request, "spec": u.spec, "spec_version": u.spec_version}
                for u in USE_CASES
            ),
            key=_canonical,
        )
    )
    docs = _sha(
        sorted(
            ({"doc_key": d.doc_key, "kind": d.kind, "content": d.content} for d in DOCS),
            key=lambda item: (item["doc_key"], item["kind"]),
        )
    )
    expected = _sha(
        {
            "format": VERSION_FORMAT,
            "business_context": business,
            "confirmed_use_cases": use_cases,
            "generated_docs": docs,
        }
    )

    result = version()

    assert (result.business_context, result.confirmed_use_cases, result.generated_docs) == (
        business,
        use_cases,
        docs,
    )
    assert result.version == expected


def test_each_component_changes_only_its_own_hash() -> None:
    base = version()

    edited = [ENTRIES[0], EntryContent(**{**ENTRIES[1].__dict__, "description": "30 days"})]
    by_context = version(entries=edited)
    by_use_case = version(use_cases=USE_CASES[:1])
    by_docs = version(docs=DOCS[:2])

    assert by_context.version != base.version
    assert by_context.confirmed_use_cases == base.confirmed_use_cases
    assert by_context.generated_docs == base.generated_docs
    assert by_use_case.version != base.version
    assert by_use_case.business_context == base.business_context
    assert by_docs.version != base.version
    assert by_docs.confirmed_use_cases == base.confirmed_use_cases
    assert len({base.version, by_context.version, by_use_case.version, by_docs.version}) == 4


def test_synonyms_and_definition_changes_change_the_version() -> None:
    base = version()
    synonyms = [EntryContent(**{**ENTRIES[0].__dict__, "synonyms": ("bought", "paid")}), ENTRIES[1]]
    definition = [
        EntryContent(**{**ENTRIES[0].__dict__, "definition": {"kind": "metric", "table": "x.y"}}),
        ENTRIES[1],
    ]

    assert version(entries=synonyms).version != base.version
    assert version(entries=definition).version != base.version


def test_empty_content_has_a_stable_version() -> None:
    assert version([], [], []) == version([], [], [])
    assert version([], [], []).version != version().version
