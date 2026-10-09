"""LLM-facing semantic context snapshot (BR-7). STUB."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from cohortsplit.crawler.sampling import SamplingPolicy
from cohortsplit.semantic.version import DocContent, EntryContent


@dataclass(frozen=True)
class UseCaseRow:
    id: int
    status: str
    nl_request: str
    spec: Mapping[str, Any]
    spec_version: str


@dataclass(frozen=True)
class PromptUseCase:
    id: int
    nl_request: str
    spec: Mapping[str, Any]
    spec_version: str


@dataclass(frozen=True)
class PromptBusinessContext:
    key: str
    kind: str
    synonyms: tuple[str, ...]
    description: str
    definition: Mapping[str, Any]


@dataclass(frozen=True)
class PromptColumn:
    name: str
    data_type: str
    nullable: bool
    comment: str | None
    allowed_values: tuple[str, ...] | None
    sample_values: tuple[str, ...]


@dataclass(frozen=True)
class PromptForeignKey:
    columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]


@dataclass(frozen=True)
class PromptTable:
    qualified_name: str
    kind: str
    comment: str | None
    columns: tuple[PromptColumn, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[PromptForeignKey, ...]
    estimated_row_count: int | None


@dataclass(frozen=True)
class SemanticContextSnapshot:
    semantic_version: str
    canonical_user_id: str | None
    business_context: tuple[PromptBusinessContext, ...]
    stale_business_context: tuple[str, ...]
    confirmed_use_cases: tuple[PromptUseCase, ...]
    tables: tuple[PromptTable, ...]


def permitted_samples(
    table_doc: Mapping[str, Any], profile: Mapping[str, Any] | None, policy: SamplingPolicy
) -> dict[str, tuple[str, ...]]:
    raise NotImplementedError


def assemble_snapshot(
    *,
    entries: Iterable[EntryContent],
    use_cases: Iterable[UseCaseRow],
    generated_docs: Iterable[DocContent],
    policy: SamplingPolicy,
) -> SemanticContextSnapshot:
    raise NotImplementedError
