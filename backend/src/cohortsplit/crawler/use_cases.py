"""Data-driven example use cases (FR-2, AC-22, AC-23).

Deterministic templates, no LLM. Each template declares the data it needs; the
generator checks the discovered schema and keeps the template, rewrites it to
the closest supported use case, or drops it, recording why. Every generated use
case is ``pending_review``.
"""

from dataclasses import dataclass
from typing import Literal

from cohortsplit.cohort_spec.draft import DraftCohortSpec
from cohortsplit.crawler.catalog import WarehouseCatalog

UseCaseStatus = Literal["pending_review", "confirmed", "rejected", "needs_rereview"]


@dataclass(frozen=True)
class GeneratedUseCase:
    key: str
    template_key: str
    nl_request: str
    spec: DraftCohortSpec
    rewritten_from: str | None = None
    generation_note: str | None = None
    status: UseCaseStatus = "pending_review"


@dataclass(frozen=True)
class DroppedTemplate:
    template_key: str
    nl_template: str
    reason: str


@dataclass(frozen=True)
class UseCaseGeneration:
    use_cases: tuple[GeneratedUseCase, ...]
    dropped: tuple[DroppedTemplate, ...]


def generate_use_cases(
    catalog: WarehouseCatalog, *, user_table: str | None = None
) -> UseCaseGeneration:
    raise NotImplementedError
