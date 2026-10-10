"""Semantic version: a deterministic content hash (FR-3, AC-25, ruling S3).

``version = sha256(canonical_json({"format", "business_context", "confirmed_use_cases",
"generated_docs"}))`` where each component is the SHA-256 of the canonical JSON of:

* business context: ``{key, kind, synonyms, description, definition}`` sorted by key;
* confirmed use cases: ``{nl_request, spec, spec_version}`` sorted by canonical JSON;
* generated docs: ``{doc_key, kind, content}`` of every generated doc sorted by
  ``(doc_key, kind)``.

Ids, timestamps, authors, origins and review notes are excluded, so the version changes
only when content that reaches the cohort compiler changes. Canonical JSON = sorted keys,
compact separators, UTF-8 (no ASCII escaping).
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

VERSION_FORMAT = 1


@dataclass(frozen=True)
class EntryContent:
    key: str
    kind: str
    synonyms: tuple[str, ...]
    description: str
    definition: Mapping[str, Any]


@dataclass(frozen=True)
class UseCaseContent:
    nl_request: str
    spec: Mapping[str, Any]
    spec_version: str


@dataclass(frozen=True)
class DocContent:
    doc_key: str
    kind: str
    content: Mapping[str, Any]


@dataclass(frozen=True)
class SemanticVersion:
    version: str
    business_context: str
    confirmed_use_cases: str
    generated_docs: str

    def components(self) -> dict[str, str]:
        return {
            "business_context": self.business_context,
            "confirmed_use_cases": self.confirmed_use_cases,
            "generated_docs": self.generated_docs,
        }


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def compute_semantic_version(
    entries: Iterable[EntryContent],
    confirmed_use_cases: Iterable[UseCaseContent],
    generated_docs: Iterable[DocContent],
) -> SemanticVersion:
    business = sorted(
        (
            {
                "key": e.key,
                "kind": e.kind,
                "synonyms": list(e.synonyms),
                "description": e.description,
                "definition": e.definition,
            }
            for e in entries
        ),
        key=lambda item: str(item["key"]),
    )
    use_cases = sorted(
        (
            {"nl_request": u.nl_request, "spec": u.spec, "spec_version": u.spec_version}
            for u in confirmed_use_cases
        ),
        key=canonical_json,
    )
    docs = sorted(
        ({"doc_key": d.doc_key, "kind": d.kind, "content": d.content} for d in generated_docs),
        key=lambda item: (str(item["doc_key"]), str(item["kind"])),
    )
    components = {
        "business_context": _sha256(business),
        "confirmed_use_cases": _sha256(use_cases),
        "generated_docs": _sha256(docs),
    }
    return SemanticVersion(version=_sha256({"format": VERSION_FORMAT, **components}), **components)
