"""Crawler service: crawl the warehouse and swap generated content into appdb.

All warehouse reads happen before appdb is touched (beyond recording the run), and
the swap is one transaction, so a crawl failing at any point leaves previous docs
and use cases intact (Failure Behavior: "Crawler fails mid-run").
"""

import logging
from dataclasses import dataclass
from typing import Any

from cohortsplit.crawler.audit import CrawlAuditHook, NullCrawlAuditHook
from cohortsplit.crawler.catalog import WarehouseCatalog
from cohortsplit.crawler.errors import CrawlScopeError
from cohortsplit.crawler.hashing import content_hash
from cohortsplit.crawler.metadata import collect_catalog
from cohortsplit.crawler.sampling import ExportGrantProvider, NoExportGrants
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.crawler.store import CrawlStore, FlaggedUseCase
from cohortsplit.crawler.use_cases import (
    DroppedTemplate,
    GeneratedUseCase,
    UseCaseGeneration,
    generate_use_cases,
)
from cohortsplit.warehouse.adapter import WarehouseAdapter
from cohortsplit.warehouse.errors import WarehouseError

logger = logging.getLogger(__name__)


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


def _sampled_columns(catalog: WarehouseCatalog) -> tuple[str, ...]:
    return tuple(
        f"{profile.table}.{column.name}"
        for profile in catalog.profiles
        for column in profile.columns
        if column.sampled
    )


def _summary(
    location: str,
    settings: CrawlerSettings,
    catalog: WarehouseCatalog,
    generation: UseCaseGeneration,
) -> dict[str, Any]:
    return {
        "location": location,
        "schemas": list(settings.schemas),
        "sampling_enabled": settings.sampling_enabled,
        "tables": len(catalog.tables),
        "columns": sum(len(t.columns) for t in catalog.tables),
        "sampled_columns": list(_sampled_columns(catalog)),
        "use_cases": [u.key for u in generation.use_cases],
        "rewritten": [u.key for u in generation.use_cases if u.rewritten_from is not None],
        "dropped": [
            {"template_key": d.template_key, "nl_template": d.nl_template, "reason": d.reason}
            for d in generation.dropped
        ],
    }


_ACTIONABLE = (WarehouseError, CrawlScopeError)


def _sanitize(exc: BaseException) -> str:
    """Warehouse and scope errors carry actionable, credential-free messages; anything
    else is reduced to its type so no unexpected detail is persisted."""
    if isinstance(exc, _ACTIONABLE):
        return str(exc)
    if not isinstance(exc, Exception):
        return f"crawl interrupted ({type(exc).__name__})"
    return f"internal error ({type(exc).__name__}); see the application logs"


def run_crawl(
    adapter: WarehouseAdapter,
    store: CrawlStore,
    *,
    settings: CrawlerSettings,
    export_grants: ExportGrantProvider | None = None,
    audit: CrawlAuditHook | None = None,
    triggered_by: str | None = None,
) -> CrawlReport:
    """Crawl, generate and atomically store. Raises :class:`CrawlFailedError` on failure."""
    audit = audit or NullCrawlAuditHook()
    grants = (export_grants or NoExportGrants()).export_granted_columns()
    policy = settings.sampling_policy(grants)
    location = adapter.describe_location()

    run_id = store.start_run(triggered_by)
    audit.crawl_started(run_id, triggered_by)
    logger.info(
        "crawl started run_id=%s location=%s schemas=%s sampling_enabled=%s",
        run_id,
        location,
        ",".join(settings.schemas) or "<all>",
        settings.sampling_enabled,
    )
    try:
        catalog = collect_catalog(
            adapter, policy, schemas=settings.schemas, user_table=settings.user_table
        )
        generation = generate_use_cases(catalog, user_table=settings.user_table)
        digest = content_hash(catalog, generation)
        swap = store.swap_generated_content(
            run_id,
            catalog=catalog,
            generation=generation,
            content_hash=digest,
            summary=_summary(location, settings, catalog, generation),
            scope_schemas=frozenset(settings.schemas) or None,
        )
    except BaseException as exc:
        message = _sanitize(exc)
        logger.error(
            "crawl failed run_id=%s location=%s error=%s",
            run_id,
            location,
            message,
            exc_info=isinstance(exc, Exception) and not isinstance(exc, _ACTIONABLE),
        )
        try:
            store.mark_run_failed(run_id, message)
        except Exception as record_exc:
            # Never let a failure to record the failure hide the crawl error.
            logger.error(
                "could not record failed crawl run_id=%s error=%s",
                run_id,
                type(record_exc).__name__,
            )
        audit.crawl_failed(run_id, triggered_by, message)
        if not isinstance(exc, Exception):
            raise  # KeyboardInterrupt / SystemExit: recorded as failed, then propagated
        raise CrawlFailedError(
            run_id,
            f"Crawl run {run_id} failed: {message} " "Previous docs and use cases are unchanged.",
        ) from None

    audit.crawl_succeeded(run_id, triggered_by, digest)
    report = CrawlReport(
        run_id=run_id,
        location=location,
        content_hash=digest,
        table_count=len(catalog.tables),
        column_count=sum(len(t.columns) for t in catalog.tables),
        sampled_columns=_sampled_columns(catalog),
        use_cases=generation.use_cases,
        dropped=generation.dropped,
        inserted_use_cases=swap.inserted_use_cases,
        preserved_keys=swap.preserved_keys,
        flagged=swap.flagged,
    )
    logger.info(
        "crawl succeeded run_id=%s tables=%s use_cases=%s inserted=%s preserved=%s "
        "flagged=%s dropped=%s content_hash=%s",
        run_id,
        report.table_count,
        len(report.use_cases),
        report.inserted_use_cases,
        len(report.preserved_keys),
        len(report.flagged),
        len(report.dropped),
        digest,
    )
    return report
