"""Crawls triggered through the API or the CLI (crawler.run).

* One crawl at a time across processes: a session-level PostgreSQL advisory lock
  (``CRAWL_RUN_LOCK_KEY``) is held for the whole crawl; a second crawl is refused.
* The crawl reuses :func:`cohortsplit.crawler.service.run_crawl` with the real
  ``RoleExportGrants`` provider (export-granted columns are never sampled) and the
  ``AuditCrawlHook`` (one ``crawler.run`` event per run).
* Afterwards the semantic version is recomputed and appended to the history when the
  crawl changed it (cause ``crawler.run``, target ``crawl_run:<id>``).
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.audit.service import Actor, AuditService
from cohortsplit.auth.crawler_integration import AuditCrawlHook, RoleExportGrants
from cohortsplit.crawler.service import CrawlReport, run_crawl
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.crawler.store import CrawlStore
from cohortsplit.semantic import repository as repo
from cohortsplit.semantic.version import SemanticVersion
from cohortsplit.warehouse.adapter import WarehouseAdapter

CRAWL_RUN_LOCK_KEY = 0x4352554E  # "CRUN"
CRAWL_CAUSE = "crawler.run"


class CrawlInProgressError(Exception):
    """Another crawl holds the crawl lock."""


@dataclass(frozen=True)
class CrawlEnvironment:
    """Where to crawl: tests override :func:`get_crawl_environment` with their own."""

    adapter_factory: Callable[[], WarehouseAdapter]
    crawler_settings: Callable[[], CrawlerSettings]


def get_crawl_environment(request: Request) -> CrawlEnvironment:
    environment: CrawlEnvironment = request.app.state.semantic.crawl_environment
    return environment


@contextmanager
def exclusive_crawl(engine: Engine) -> Iterator[None]:
    """Hold the crawl lock for the block; raise :class:`CrawlInProgressError` if taken.

    Session-level advisory lock on a dedicated connection, released before the
    connection returns to the pool (and by PostgreSQL if the connection dies).
    """
    if engine.dialect.name != "postgresql":
        yield  # single-process test databases
        return
    with engine.connect() as conn:
        acquired = bool(
            conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": CRAWL_RUN_LOCK_KEY}
            ).scalar()
        )
        conn.commit()
        if not acquired:
            raise CrawlInProgressError()
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": CRAWL_RUN_LOCK_KEY})
            conn.commit()


def record_crawl_version(
    session_factory: sessionmaker[Session],
    run_id: int,
    *,
    actor_user_id: int | None,
    now: datetime,
) -> SemanticVersion:
    """Recompute the semantic version after a crawl and record it if it changed."""
    with session_factory() as db, db.begin():
        repo.lock_semantic_content(db)
        version = repo.compute_current_version(db)
        repo.record_version_if_changed(
            db,
            version,
            cause=CRAWL_CAUSE,
            target=f"crawl_run:{run_id}",
            actor_user_id=actor_user_id,
            now=now,
        )
    return version


@dataclass(frozen=True)
class CrawlOutcome:
    report: CrawlReport
    semantic_version: SemanticVersion


class CrawlCoordinator:
    def __init__(self, engine: Engine, audit: AuditService) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(engine, expire_on_commit=False)
        self._audit = audit

    def run(
        self,
        environment: CrawlEnvironment,
        *,
        actor: Actor,
        triggered_by: str,
        now: Callable[[], datetime],
        on_start: Callable[[], None],
    ) -> CrawlOutcome:
        """Crawl under the lock. ``on_start`` runs once the lock is held (e.g. a fail-closed
        audit write) and may raise to abort before the warehouse is touched.

        Raises :class:`CrawlInProgressError`, or ``CrawlFailedError`` from ``run_crawl``.
        """
        settings = environment.crawler_settings()
        adapter = environment.adapter_factory()
        with exclusive_crawl(self._engine):
            on_start()
            report = run_crawl(
                adapter,
                CrawlStore(self._engine),
                settings=settings,
                export_grants=RoleExportGrants(self._session_factory),
                audit=AuditCrawlHook(self._audit, actor, clock=now),
                triggered_by=triggered_by,
            )
            version = record_crawl_version(
                self._session_factory, report.run_id, actor_user_id=actor.user_id, now=now()
            )
        return CrawlOutcome(report=report, semantic_version=version)
