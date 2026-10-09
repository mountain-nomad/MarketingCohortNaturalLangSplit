"""Auth <-> crawler integration.

* :class:`CrawlStoreColumnInventory` — columns of the latest crawl, so the role editor can
  mark export grants on columns that no longer exist as "missing" (FR-A4). A missing
  grant is inert: the export gate still only allows granted columns, and a column absent
  from the warehouse cannot be exported anyway.
* :class:`RoleExportGrants` — the crawler's ``ExportGrantProvider``: export-granted
  columns are never sampled (crawler ruling R2).
* :class:`AuditCrawlHook` — the crawler's ``CrawlAuditHook``: one ``crawler.run`` audit
  event per run (FR-A5).
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.audit import actions
from cohortsplit.audit.service import Actor, AuditEventIn, AuditService, Outcome
from cohortsplit.auth.clock import utcnow
from cohortsplit.auth.models import RoleExportColumn
from cohortsplit.crawler.store import CrawlStore

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500


class ColumnInventory(Protocol):
    def columns(self) -> frozenset[str] | None:
        """``schema.table.column`` of every crawled column, or None if unknown."""


class CrawlStoreColumnInventory:
    def __init__(self, store: CrawlStore) -> None:
        self._store = store

    def columns(self) -> frozenset[str] | None:
        try:
            docs = self._store.list_docs(origin="generated", kind="table_schema")
        except SQLAlchemyError:
            logger.warning("crawler tables unreadable; export grant status unknown")
            return None
        if not docs:
            return None  # never crawled: nothing can be called missing yet
        found: set[str] = set()
        for doc in docs:
            content = doc.content
            schema, table = content.get("schema_name"), content.get("name")
            for column in content.get("columns") or ():
                name = column.get("name") if isinstance(column, dict) else None
                if schema and table and name:
                    found.add(f"{schema}.{table}.{name}")
        return frozenset(found)


class _UnknownInventory:
    def columns(self) -> frozenset[str] | None:
        return None


def get_column_inventory(request: Request) -> ColumnInventory:
    inventory: ColumnInventory | None = request.app.state.auth.column_inventory
    return inventory or _UnknownInventory()


class RoleExportGrants:
    """Union of every role's export-column grants (members or not: fail closed)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def export_granted_columns(self) -> frozenset[str]:
        with self._session_factory() as db:
            return frozenset(db.execute(select(RoleExportColumn.column_ref)).scalars())


class AuditCrawlHook:
    def __init__(
        self, audit: AuditService, actor: Actor, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._audit = audit
        self._actor = actor
        self._clock = clock or utcnow

    def _record(self, run_id: int, outcome: Outcome, metadata: dict[str, object]) -> None:
        self._audit.record_detached(
            AuditEventIn(
                action=actions.CRAWLER_RUN,
                outcome=outcome,
                actor=self._actor,
                occurred_at=self._clock(),
                target_type="crawl_run",
                target_id=str(run_id),
                metadata=metadata,
            ),
            sensitive=False,
        )

    def crawl_started(self, run_id: int, triggered_by: str | None) -> None:
        return None  # one event per run, written when it ends

    def crawl_succeeded(self, run_id: int, triggered_by: str | None, content_hash: str) -> None:
        self._record(
            run_id, "success", {"triggered_by": triggered_by, "content_hash": content_hash}
        )

    def crawl_failed(self, run_id: int, triggered_by: str | None, error: str) -> None:
        self._record(
            run_id, "error", {"triggered_by": triggered_by, "error": error[:MAX_ERROR_LENGTH]}
        )
