"""Crawler service: crawl the warehouse and swap generated content into appdb."""

from dataclasses import dataclass

from cohortsplit.crawler.audit import CrawlAuditHook, NullCrawlAuditHook
from cohortsplit.crawler.sampling import ExportGrantProvider, NoExportGrants
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.crawler.store import CrawlStore, FlaggedUseCase
from cohortsplit.crawler.use_cases import DroppedTemplate, GeneratedUseCase
from cohortsplit.warehouse.adapter import WarehouseAdapter


class CrawlFailedError(Exception):
    """The crawl failed; previous content is intact. The message is sanitized."""

    def __init__(self, run_id: int, message: str) -> None:
        super().__init__(message)
        self.run_id = run_id


@dataclass(frozen=True)
class CrawlReport:
    run_id: int
    location: str
    content_hash: str
    table_count: int
    column_count: int
    sampled_columns: tuple[str, ...]
    use_cases: tuple[GeneratedUseCase, ...]
    dropped: tuple[DroppedTemplate, ...]
    inserted_use_cases: int
    preserved_keys: tuple[str, ...]
    flagged: tuple[FlaggedUseCase, ...]


def run_crawl(
    adapter: WarehouseAdapter,
    store: CrawlStore,
    *,
    settings: CrawlerSettings,
    export_grants: ExportGrantProvider | None = None,
    audit: CrawlAuditHook | None = None,
    triggered_by: str | None = None,
) -> CrawlReport:
    raise NotImplementedError


_ = (NoExportGrants, NullCrawlAuditHook)
