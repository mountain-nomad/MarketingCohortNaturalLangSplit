"""Persistence of crawler output in appdb.

Re-run rules (FR-2, AC-25 crawler half, AC-26):
- generated docs are replaced wholesale; human docs are never touched;
- generated ``pending_review`` use cases are replaced; generated use cases a human
  already reviewed (confirmed / rejected / needs_rereview) are kept and their key is
  not regenerated; human use cases are never touched;
- confirmed use cases (any origin) that reference a table/column that no longer
  exists in the crawled scope become ``needs_rereview`` with a note naming what is missing;
- all of the above happens in ONE transaction, so a failure leaves previous content intact.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from cohortsplit.cohort_spec.draft import DraftCohortSpec
from cohortsplit.crawler.catalog import WarehouseCatalog
from cohortsplit.crawler.use_cases import UseCaseGeneration, UseCaseStatus


@dataclass(frozen=True)
class CrawlRun:
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    triggered_by: str | None
    content_hash: str | None
    summary: dict[str, Any]
    error: str | None


@dataclass(frozen=True)
class StoredDoc:
    id: int
    doc_key: str
    kind: str
    origin: str
    content: dict[str, Any]
    crawl_run_id: int | None
    updated_at: datetime


@dataclass(frozen=True)
class StoredUseCase:
    id: int
    use_case_key: str
    origin: str
    status: str
    nl_request: str
    spec: dict[str, Any]
    spec_version: str
    template_key: str | None
    rewritten_from: str | None
    generation_note: str | None
    review_note: str | None
    referenced_columns: tuple[str, ...]
    crawl_run_id: int | None
    updated_at: datetime


@dataclass(frozen=True)
class FlaggedUseCase:
    id: int
    nl_request: str
    missing: tuple[str, ...]


@dataclass(frozen=True)
class SwapResult:
    inserted_use_cases: int
    preserved_keys: tuple[str, ...]
    flagged: tuple[FlaggedUseCase, ...]


class CrawlStore:
    def __init__(self, engine: Engine) -> None:
        raise NotImplementedError

    def start_run(self, triggered_by: str | None) -> int:
        raise NotImplementedError

    def mark_run_failed(self, run_id: int, error: str) -> None:
        raise NotImplementedError

    def swap_generated_content(
        self,
        run_id: int,
        *,
        catalog: WarehouseCatalog,
        generation: UseCaseGeneration,
        content_hash: str,
        summary: dict[str, Any],
        scope_schemas: frozenset[str] | None,
    ) -> SwapResult:
        """Replace generated content atomically. ``scope_schemas=None`` = full crawl."""
        raise NotImplementedError

    def get_run(self, run_id: int) -> CrawlRun | None:
        raise NotImplementedError

    def latest_run(self) -> CrawlRun | None:
        raise NotImplementedError

    def list_docs(self, *, origin: str | None = None, kind: str | None = None) -> list[StoredDoc]:
        raise NotImplementedError

    def list_use_cases(
        self, *, origin: str | None = None, status: str | None = None
    ) -> list[StoredUseCase]:
        raise NotImplementedError

    def list_confirmed_use_cases(self) -> list[StoredUseCase]:
        """The only use cases allowed to influence interpretation (BR-7)."""
        raise NotImplementedError

    def add_human_use_case(
        self, nl_request: str, spec: DraftCohortSpec, status: UseCaseStatus = "pending_review"
    ) -> int:
        raise NotImplementedError

    def upsert_human_doc(self, doc_key: str, kind: str, content: dict[str, Any]) -> int:
        raise NotImplementedError

    def set_use_case_status(
        self, use_case_id: int, status: UseCaseStatus, review_note: str | None = None
    ) -> None:
        raise NotImplementedError
