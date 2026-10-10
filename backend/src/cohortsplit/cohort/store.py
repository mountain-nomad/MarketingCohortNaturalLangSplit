"""Interpretations and prepared cohorts: the interface the experiment-split branch consumes.

A **prepared cohort** is what preview leaves behind for the split step: the confirmed
definition (NL request, spec, spec hash), the compiled read-only SQL with its definition
parameters and the execution time it was previewed at, the semantic version, the row
count and the export columns the creator selected. It never holds membership, rows or
export values (ruling C4).

Usage (feature/experiment-split)::

    prepared = PreparedCohortStore().get(db, prepared_id, user_id=principal.user_id, now=now)
    if prepared is None: ...                       # unknown, expired or someone else's
    member_ids = execute_membership(executor, prepared, allowed_tables=...)   # re-validated

``execute_membership`` re-validates the stored SQL with the AST validator and runs it
through the read-only executor with the stored ``as_of``, so the same definition is
evaluated at the same reference time (data may still have changed since).
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from cohortsplit.cohort.environment import QueryExecutor
from cohortsplit.cohort.models import CohortInterpretation, PreparedCohortRow
from cohortsplit.compiler.validator import validate_select


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class PreparedCohort:
    id: str
    created_by: int
    created_at: datetime
    expires_at: datetime
    nl_request: str
    spec: Mapping[str, Any]
    spec_hash: str
    sql: str
    params: Mapping[str, Any]
    sql_hash: str
    semantic_version: str
    dialect: str
    as_of: datetime
    row_count: int
    export_columns: tuple[str, ...]


def _prepared(row: PreparedCohortRow) -> PreparedCohort:
    return PreparedCohort(
        id=row.id,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        nl_request=row.nl_request,
        spec=dict(row.spec),
        spec_hash=row.spec_hash,
        sql=row.sql,
        params=dict(row.params),
        sql_hash=row.sql_hash,
        semantic_version=row.semantic_version,
        dialect=row.dialect,
        as_of=row.as_of,
        row_count=row.row_count,
        export_columns=tuple(row.export_columns),
    )


class PreparedCohortStore:
    def get(
        self, db: Session, prepared_id: str, *, user_id: int, now: datetime
    ) -> PreparedCohort | None:
        """The creator's prepared cohort, or None (unknown, expired or someone else's)."""
        row = db.get(PreparedCohortRow, prepared_id)
        if row is None or row.created_by != user_id or row.expires_at <= now:
            return None
        return _prepared(row)


def execute_membership(
    executor: QueryExecutor, prepared: PreparedCohort, *, allowed_tables: Iterable[str]
) -> tuple[object, ...]:
    """Canonical user ids of the prepared cohort, re-validated and executed read-only."""
    validate_select(prepared.sql, allowed_tables=allowed_tables, dialect=prepared.dialect)
    result = executor.execute(prepared.sql, prepared.params)
    return tuple(row[0] for row in result.rows)


def get_interpretation(
    db: Session, interpretation_id: str, *, user_id: int, now: datetime
) -> CohortInterpretation | None:
    row = db.get(CohortInterpretation, interpretation_id)
    if row is None or row.created_by != user_id or row.expires_at <= now:
        return None
    return row


def purge_expired(db: Session, now: datetime, *, grace: timedelta = timedelta(0)) -> None:
    """Delete expired interpretations and prepared cohorts (opportunistic housekeeping)."""
    cutoff = now - grace
    db.execute(delete(PreparedCohortRow).where(PreparedCohortRow.expires_at <= cutoff))
    db.execute(delete(CohortInterpretation).where(CohortInterpretation.expires_at <= cutoff))
