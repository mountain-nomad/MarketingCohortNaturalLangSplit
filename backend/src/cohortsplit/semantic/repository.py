"""Reads and writes of semantic content on a SQLAlchemy ``Session``.

Business context and the version history are ORM models; crawler output (docs, use
cases, runs) is read and updated through the crawler's Core tables in the same session,
so an edit, its audit event and its version record share one transaction.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, func, select, text, update
from sqlalchemy.orm import Session

from cohortsplit.auth.models import User
from cohortsplit.crawler.store import SEMANTIC_CONTENT_LOCK_KEY
from cohortsplit.crawler.tables import crawl_runs, example_use_cases, semantic_docs
from cohortsplit.semantic.inventory import SchemaInventory
from cohortsplit.semantic.models import BusinessContextEntry, SemanticVersionRecord
from cohortsplit.semantic.snapshot import TABLE_SCHEMA, UseCaseRow
from cohortsplit.semantic.version import (
    DocContent,
    EntryContent,
    SemanticVersion,
    UseCaseContent,
    compute_semantic_version,
)

CONFIRMED = "confirmed"


def lock_semantic_content(db: Session) -> None:
    """Serialize semantic changes with crawl swaps (transaction-scoped; PostgreSQL only)."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SEMANTIC_CONTENT_LOCK_KEY})


# -- generated docs ------------------------------------------------------------------------


def generated_docs(db: Session) -> list[DocContent]:
    rows = db.execute(
        select(semantic_docs.c.doc_key, semantic_docs.c.kind, semantic_docs.c.content)
        .where(semantic_docs.c.origin == "generated")
        .order_by(semantic_docs.c.doc_key, semantic_docs.c.kind)
    ).all()
    return [DocContent(doc_key=r.doc_key, kind=r.kind, content=dict(r.content)) for r in rows]


def schema_inventory(docs: Iterable[DocContent]) -> SchemaInventory | None:
    """Inventory of the latest crawl, or None when nothing was ever crawled."""
    tables = [d.content for d in docs if d.kind == TABLE_SCHEMA]
    return SchemaInventory.from_table_docs(tables) if tables else None


def latest_run(db: Session) -> Row[Any] | None:
    return db.execute(select(crawl_runs).order_by(crawl_runs.c.id.desc()).limit(1)).first()


def list_runs(db: Session, limit: int) -> list[Row[Any]]:
    return list(db.execute(select(crawl_runs).order_by(crawl_runs.c.id.desc()).limit(limit)).all())


# -- business context ----------------------------------------------------------------------


def list_entries(db: Session) -> list[BusinessContextEntry]:
    return list(db.scalars(select(BusinessContextEntry).order_by(BusinessContextEntry.key)))


def get_entry(db: Session, key: str) -> BusinessContextEntry | None:
    return db.scalars(select(BusinessContextEntry).where(BusinessContextEntry.key == key)).first()


def entry_content(entry: BusinessContextEntry) -> EntryContent:
    return EntryContent(
        key=entry.key,
        kind=entry.kind,
        synonyms=tuple(entry.synonyms),
        description=entry.description,
        definition=dict(entry.definition),
    )


# -- use cases -----------------------------------------------------------------------------


def list_use_cases(db: Session, status: str | None = None) -> list[Row[Any]]:
    query = select(example_use_cases).order_by(example_use_cases.c.id)
    if status is not None:
        query = query.where(example_use_cases.c.status == status)
    return list(db.execute(query).all())


def get_use_case(db: Session, use_case_id: int, *, for_update: bool = False) -> Row[Any] | None:
    query = select(example_use_cases).where(example_use_cases.c.id == use_case_id)
    if for_update and db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    return db.execute(query).first()


def update_use_case(db: Session, use_case_id: int, values: dict[str, Any]) -> None:
    db.execute(
        update(example_use_cases).where(example_use_cases.c.id == use_case_id).values(**values)
    )


def use_case_rows(db: Session) -> list[UseCaseRow]:
    rows = db.execute(
        select(
            example_use_cases.c.id,
            example_use_cases.c.status,
            example_use_cases.c.nl_request,
            example_use_cases.c.spec,
            example_use_cases.c.spec_version,
        ).order_by(example_use_cases.c.id)
    ).all()
    return [
        UseCaseRow(
            id=r.id,
            status=r.status,
            nl_request=r.nl_request,
            spec=dict(r.spec),
            spec_version=r.spec_version,
        )
        for r in rows
    ]


# -- semantic version ----------------------------------------------------------------------


def compute_current_version(db: Session) -> SemanticVersion:
    """The semantic version of the content currently stored (read in ``db``'s transaction)."""
    confirmed = [
        UseCaseContent(r.nl_request, r.spec, r.spec_version)
        for r in use_case_rows(db)
        if r.status == CONFIRMED
    ]
    return compute_semantic_version(
        [entry_content(e) for e in list_entries(db)], confirmed, generated_docs(db)
    )


def latest_version_record(db: Session) -> SemanticVersionRecord | None:
    return db.scalars(
        select(SemanticVersionRecord).order_by(SemanticVersionRecord.id.desc()).limit(1)
    ).first()


def record_version_if_changed(
    db: Session,
    version: SemanticVersion,
    *,
    cause: str,
    target: str | None,
    actor_user_id: int | None,
    now: datetime,
) -> bool:
    """Append ``version`` to the history unless it equals the latest recorded version."""
    latest = latest_version_record(db)
    if latest is not None and latest.version == version.version:
        return False
    db.add(
        SemanticVersionRecord(
            version=version.version,
            components=version.components(),
            cause=cause,
            target=target,
            actor_type="user" if actor_user_id is not None else "cli",
            actor_user_id=actor_user_id,
            created_at=now,
        )
    )
    db.flush()
    return True


def version_history(
    db: Session, *, limit: int, offset: int
) -> tuple[list[SemanticVersionRecord], int]:
    total = db.scalar(select(func.count()).select_from(SemanticVersionRecord)) or 0
    items = list(
        db.scalars(
            select(SemanticVersionRecord)
            .order_by(SemanticVersionRecord.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total


# -- users ---------------------------------------------------------------------------------


def user_refs(db: Session, ids: Sequence[int | None]) -> dict[int, dict[str, Any]]:
    wanted = sorted({i for i in ids if i is not None})
    if not wanted:
        return {}
    rows = db.execute(select(User.id, User.display_name).where(User.id.in_(wanted))).all()
    return {r.id: {"id": r.id, "display_name": r.display_name} for r in rows}
