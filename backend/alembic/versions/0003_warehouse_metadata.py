"""Warehouse metadata: crawl runs, generated/human semantic docs, example use cases.

Revision ID: 0003_warehouse_metadata
Revises: 0001_baseline
Create Date: 2026-10-09

Note: chained to the baseline; whichever of feature/authentication-rbac and
feature/warehouse-crawler merges second re-chains its down_revision.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_warehouse_metadata"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("triggered_by", sa.Text),
        sa.Column("content_hash", sa.String(64)),
        sa.Column(
            "summary", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("error", sa.Text),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="crawl_runs_status_check"
        ),
    )
    op.create_index("crawl_runs_started_at_idx", "crawl_runs", ["started_at"])

    op.create_table(
        "semantic_docs",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("doc_key", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("content", postgresql.JSONB, nullable=False),
        sa.Column(
            "crawl_run_id", sa.BigInteger, sa.ForeignKey("crawl_runs.id", ondelete="SET NULL")
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('table_schema', 'data_profile', 'note')", name="semantic_docs_kind_check"
        ),
        sa.CheckConstraint("origin IN ('generated', 'human')", name="semantic_docs_origin_check"),
        sa.UniqueConstraint("doc_key", "kind", "origin", name="semantic_docs_key_kind_origin_key"),
    )
    op.create_index("semantic_docs_crawl_run_id_idx", "semantic_docs", ["crawl_run_id"])

    op.create_table(
        "example_use_cases",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("use_case_key", sa.Text, nullable=False),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("nl_request", sa.Text, nullable=False),
        sa.Column("spec", postgresql.JSONB, nullable=False),
        sa.Column("spec_version", sa.Text, nullable=False),
        sa.Column("template_key", sa.Text),
        sa.Column("rewritten_from", sa.Text),
        sa.Column("generation_note", sa.Text),
        sa.Column("review_note", sa.Text),
        sa.Column(
            "referenced_columns",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "crawl_run_id", sa.BigInteger, sa.ForeignKey("crawl_runs.id", ondelete="SET NULL")
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "origin IN ('generated', 'human')", name="example_use_cases_origin_check"
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'confirmed', 'rejected', 'needs_rereview')",
            name="example_use_cases_status_check",
        ),
    )
    op.create_index(
        "example_use_cases_generated_key_idx",
        "example_use_cases",
        ["use_case_key"],
        unique=True,
        postgresql_where=sa.text("origin = 'generated'"),
    )
    op.create_index("example_use_cases_status_idx", "example_use_cases", ["status"])
    op.create_index("example_use_cases_crawl_run_id_idx", "example_use_cases", ["crawl_run_id"])


def downgrade() -> None:
    op.drop_table("example_use_cases")
    op.drop_table("semantic_docs")
    op.drop_table("crawl_runs")
