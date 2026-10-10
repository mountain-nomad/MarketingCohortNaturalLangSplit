"""ORM models for interpretations and prepared cohorts (migration ``0005_cohort_compiler``).

Both are short-lived and hold **definitions only**: the NL request, the spec, hashes, the
compiled SQL with its definition parameters and the semantic version. Cohort result rows,
membership and export values are never stored here (ruling C4).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cohortsplit.orm import Base, BigId, JsonDocument, UTCDateTime


class CohortInterpretation(Base):
    """An interpretation shown to its creator, awaiting confirmation (FR-5)."""

    __tablename__ = "cohort_interpretations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_by: Mapped[int] = mapped_column(
        BigId, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    nl_request: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(64), nullable=False)
    readable: Mapped[str] = mapped_column(Text, nullable=False)


class PreparedCohortRow(Base):
    """A confirmed, previewed cohort definition for the experiment-split step."""

    __tablename__ = "prepared_cohorts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    interpretation_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("cohort_interpretations.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[int] = mapped_column(
        BigId, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    nl_request: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    sql_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(64), nullable=False)
    dialect: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    export_columns: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False)
