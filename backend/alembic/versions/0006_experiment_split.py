"""Experiment split: runs and their membership snapshots (FR-8, FR-9a).

Revision ID: 0006_experiment_split
Revises: 0005_cohort_compiler
Create Date: 2026-10-12

* ``experiment_runs`` — one row per split: the definition it was made from, the
  assignment parameters (key, type, allocation, assignment version), per-group counts,
  the export columns chosen at split time and how often it was downloaded.
* ``experiment_run_members`` — the membership snapshot: canonical user id and group
  only (AC-21c).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_experiment_split"
down_revision: str | None = "0005_cohort_compiler"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        _ts("created_at"),
        sa.Column("prepared_cohort_id", sa.String(32), nullable=True),
        sa.Column("nl_request", sa.Text(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("spec_hash", sa.String(64), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("sql_params", postgresql.JSONB(), nullable=False),
        sa.Column("sql_hash", sa.String(64), nullable=False),
        sa.Column("dialect", sa.Text(), nullable=False),
        _ts("as_of"),
        sa.Column("semantic_version", sa.String(64), nullable=False),
        sa.Column("canonical_user_id", sa.Text(), nullable=False),
        sa.Column("user_id_type", sa.Text(), nullable=False),
        sa.Column("experiment_key", sa.String(100), nullable=False),
        sa.Column("experiment_type", sa.Text(), nullable=False),
        sa.Column("test_percent", sa.Integer(), nullable=False),
        sa.Column("control_percent", sa.Integer(), nullable=False),
        sa.Column("assignment_version", sa.Integer(), nullable=False),
        sa.Column("test_count", sa.Integer(), nullable=False),
        sa.Column("control_count", sa.Integer(), nullable=False),
        sa.Column("export_columns", postgresql.JSONB(), nullable=False),
        sa.Column("download_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_runs"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_experiment_runs_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("test_percent BETWEEN 1 AND 99", name="ck_experiment_runs_test_percent"),
        sa.CheckConstraint(
            "control_percent BETWEEN 1 AND 99", name="ck_experiment_runs_control_percent"
        ),
        sa.CheckConstraint(
            "test_percent + control_percent = 100", name="ck_experiment_runs_allocation_sum"
        ),
        sa.CheckConstraint(
            "experiment_type IN ('ab', 'aa')", name="ck_experiment_runs_experiment_type"
        ),
        sa.CheckConstraint(
            "user_id_type IN ('integer', 'string')", name="ck_experiment_runs_user_id_type"
        ),
        sa.CheckConstraint(
            "test_count >= 0 AND control_count >= 0", name="ck_experiment_runs_counts"
        ),
        sa.CheckConstraint("download_count >= 0", name="ck_experiment_runs_download_count"),
    )
    op.create_index(
        "ix_experiment_runs_created_by_created_at",
        "experiment_runs",
        ["created_by", "created_at"],
    )
    op.create_index("ix_experiment_runs_created_at", "experiment_runs", ["created_at"])

    op.create_table(
        "experiment_run_members",
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("canonical_user_id", sa.Text(), nullable=False),
        sa.Column("assigned_group", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "position", name="pk_experiment_run_members"),
        sa.UniqueConstraint(
            "run_id", "canonical_user_id", name="uq_experiment_run_members_run_id_user"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["experiment_runs.id"],
            name="fk_experiment_run_members_run_id_experiment_runs",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "assigned_group IN ('test', 'control')",
            name="ck_experiment_run_members_assigned_group",
        ),
    )


def downgrade() -> None:
    op.drop_table("experiment_run_members")
    op.drop_index("ix_experiment_runs_created_at", table_name="experiment_runs")
    op.drop_index("ix_experiment_runs_created_by_created_at", table_name="experiment_runs")
    op.drop_table("experiment_runs")
