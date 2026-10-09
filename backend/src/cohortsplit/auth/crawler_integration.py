"""Auth <-> crawler integration (stubs)."""

from collections.abc import Callable
from datetime import datetime

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.audit.service import Actor, AuditService
from cohortsplit.crawler.store import CrawlStore


class CrawlStoreColumnInventory:
    def __init__(self, store: CrawlStore) -> None:
        self._store = store

    def columns(self) -> frozenset[str] | None:
        raise NotImplementedError


def get_column_inventory(request: Request) -> CrawlStoreColumnInventory:
    raise NotImplementedError


class RoleExportGrants:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def export_granted_columns(self) -> frozenset[str]:
        raise NotImplementedError


class AuditCrawlHook:
    def __init__(
        self, audit: AuditService, actor: Actor, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._audit = audit

    def crawl_started(self, run_id: int, triggered_by: str | None) -> None:
        raise NotImplementedError

    def crawl_succeeded(self, run_id: int, triggered_by: str | None, content_hash: str) -> None:
        raise NotImplementedError

    def crawl_failed(self, run_id: int, triggered_by: str | None, error: str) -> None:
        raise NotImplementedError
