"""ORM models for experiment runs and their membership snapshots (``0006_experiment_split``).

A run keeps the definition it was split from (NL request, spec, SQL, semantic version),
the assignment parameters and per-group counts. The membership snapshot stores, per
cohort member, **only** the canonical user id and the assigned group (AC-21c): no PII,
no export values, no other columns.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from cohortsplit.orm import Base, BigId, JsonDocument, UTCDateTime


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        CheckConstraint("test_percent BETWEEN 1 AND 99", name="test_percent"),
        CheckConstraint("control_percent BETWEEN 1 AND 99", name="control_percent"),
        CheckConstraint("test_percent + control_percent = 100", name="allocation_sum"),
        CheckConstraint("experiment_type IN ('ab', 'aa')", name="experiment_type"),
        CheckConstraint("user_id_type IN ('integer', 'string')", name="user_id_type"),
        CheckConstraint("test_count >= 0 AND control_count >= 0", name="counts"),
        CheckConstraint("download_count >= 0", name="download_count"),
        Index("ix_experiment_runs_created_by_created_at", "created_by", "created_at"),
        Index("ix_experiment_runs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Users are never hard-deleted (FR-A2); RESTRICT keeps every run attributable.
    created_by: Mapped[int] = mapped_column(
        BigId, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # Prepared cohorts expire and are purged; the run keeps its own copy of the definition.
    prepared_cohort_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nl_request: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    sql_params: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    sql_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dialect: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id_type: Mapped[str] = mapped_column(Text, nullable=False)
    experiment_key: Mapped[str] = mapped_column(String(100), nullable=False)
    experiment_type: Mapped[str] = mapped_column(Text, nullable=False)
    test_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    control_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    test_count: Mapped[int] = mapped_column(Integer, nullable=False)
    control_count: Mapped[int] = mapped_column(Integer, nullable=False)
    export_columns: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ExperimentRunMember(Base):
    """One cohort member of a run: canonical user id and group, nothing else."""

    __tablename__ = "experiment_run_members"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", "position", name="pk_experiment_run_members"),
        UniqueConstraint(
            "run_id", "canonical_user_id", name="uq_experiment_run_members_run_id_user"
        ),
        CheckConstraint("assigned_group IN ('test', 'control')", name="assigned_group"),
    )

    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("experiment_runs.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_group: Mapped[str] = mapped_column(Text, nullable=False)
