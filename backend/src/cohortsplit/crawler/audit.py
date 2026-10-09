"""Audit seam for crawler runs.

The audit log arrives with ``feature/authentication-rbac``. It should implement
:class:`CrawlAuditHook` and be passed to :func:`cohortsplit.crawler.service.run_crawl`;
until then :class:`NullCrawlAuditHook` is used. Hooks receive ids and sanitized
messages only, never credentials or sample values.
"""

from typing import Protocol


class CrawlAuditHook(Protocol):
    def crawl_started(self, run_id: int, triggered_by: str | None) -> None: ...

    def crawl_succeeded(self, run_id: int, triggered_by: str | None, content_hash: str) -> None: ...

    def crawl_failed(self, run_id: int, triggered_by: str | None, error: str) -> None: ...


class NullCrawlAuditHook:
    def crawl_started(self, run_id: int, triggered_by: str | None) -> None:
        return None

    def crawl_succeeded(self, run_id: int, triggered_by: str | None, content_hash: str) -> None:
        return None

    def crawl_failed(self, run_id: int, triggered_by: str | None, error: str) -> None:
        return None
