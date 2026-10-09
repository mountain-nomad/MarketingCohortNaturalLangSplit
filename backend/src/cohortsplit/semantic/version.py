"""Semantic version: deterministic content hash. STUB."""

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


def compute_semantic_version(
    entries: Iterable[EntryContent],
    confirmed_use_cases: Iterable[UseCaseContent],
    generated_docs: Iterable[DocContent],
) -> SemanticVersion:
    raise NotImplementedError
