"""Cohort compiler: interpretations, prepared cohorts, draft-0 use cases migrated to spec v1.

Revision ID: 0005_cohort_compiler
Revises: 0004_semantic_context
Create Date: 2026-10-10

* ``cohort_interpretations`` — short-lived interpretations awaiting confirmation (FR-5).
* ``prepared_cohorts`` — confirmed, previewed cohort definitions for the split step;
  definitions only, never rows (ruling C4).
* Data: ``example_use_cases`` rows with ``spec_version = 'draft-0'`` are rewritten to cohort
  spec v1 (crawler ruling R1, ruling C2) and ``referenced_columns`` recomputed. The original
  documents are kept in ``spec_draft0_backup`` so the downgrade restores them exactly; rows
  written as v1 after the upgrade have no draft-0 form and are flagged ``needs_rereview``.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from cohortsplit.cohort_spec.draft import DraftCohortSpec
from cohortsplit.cohort_spec.upgrade import upgrade_draft0
from cohortsplit.cohort_spec.v1 import referenced_columns, spec_document

revision: str = "0005_cohort_compiler"
down_revision: str | None = "0004_semantic_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V1_ONLY_NOTE = (
    "Written as cohort spec v1, which this application version cannot read; "
    "re-review after the downgrade."
)


def _ts(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "cohort_interpretations",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        _ts("created_at"),
        _ts("expires_at"),
        sa.Column("nl_request", sa.Text(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("spec_hash", sa.String(64), nullable=False),
        sa.Column("semantic_version", sa.String(64), nullable=False),
        sa.Column("readable", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_cohort_interpretations"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_cohort_interpretations_created_by_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_cohort_interpretations_created_by", "cohort_interpretations", ["created_by"]
    )
    op.create_index(
        "ix_cohort_interpretations_expires_at", "cohort_interpretations", ["expires_at"]
    )

    op.create_table(
        "prepared_cohorts",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("interpretation_id", sa.String(32), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        _ts("created_at"),
        _ts("expires_at"),
        sa.Column("nl_request", sa.Text(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("spec_hash", sa.String(64), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("sql_hash", sa.String(64), nullable=False),
        sa.Column("semantic_version", sa.String(64), nullable=False),
        sa.Column("dialect", sa.Text(), nullable=False),
        _ts("as_of"),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("export_columns", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_prepared_cohorts"),
        sa.ForeignKeyConstraint(
            ["interpretation_id"],
            ["cohort_interpretations.id"],
            name="fk_prepared_cohorts_interpretation_id_cohort_interpretations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_prepared_cohorts_created_by_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_prepared_cohorts_created_by", "prepared_cohorts", ["created_by"])
    op.create_index("ix_prepared_cohorts_expires_at", "prepared_cohorts", ["expires_at"])

    op.create_table(
        "spec_draft0_backup",
        sa.Column("use_case_id", sa.BigInteger(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("referenced_columns", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("use_case_id", name="pk_spec_draft0_backup"),
        sa.ForeignKeyConstraint(
            ["use_case_id"],
            ["example_use_cases.id"],
            name="fk_spec_draft0_backup_use_case_id_example_use_cases",
            ondelete="CASCADE",
        ),
    )
    _upgrade_specs()


def _upgrade_specs() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, spec, referenced_columns FROM example_use_cases "
            "WHERE spec_version = 'draft-0' ORDER BY id"
        )
    ).all()
    for row in rows:
        try:
            upgraded = upgrade_draft0(DraftCohortSpec.model_validate(dict(row.spec)))
        except ValidationError:
            continue  # unreadable draft: left as is (load_spec refuses it; reviewers fix it)
        conn.execute(
            sa.text(
                "INSERT INTO spec_draft0_backup (use_case_id, spec, referenced_columns) "
                "VALUES (:id, CAST(:spec AS jsonb), CAST(:columns AS jsonb))"
            ),
            {
                "id": row.id,
                "spec": json.dumps(row.spec),
                "columns": json.dumps(list(row.referenced_columns)),
            },
        )
        conn.execute(
            sa.text(
                "UPDATE example_use_cases SET spec = CAST(:spec AS jsonb), spec_version = '1', "
                "referenced_columns = CAST(:columns AS jsonb) WHERE id = :id"
            ),
            {
                "id": row.id,
                "spec": json.dumps(spec_document(upgraded)),
                "columns": json.dumps(sorted(referenced_columns(upgraded))),
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    backups = conn.execute(
        sa.text("SELECT use_case_id, spec, referenced_columns FROM spec_draft0_backup")
    ).all()
    for backup in backups:
        columns = list(backup.referenced_columns)
        conn.execute(
            sa.text(
                "UPDATE example_use_cases SET spec = CAST(:spec AS jsonb), "
                "spec_version = 'draft-0', referenced_columns = CAST(:columns AS jsonb) "
                "WHERE id = :id"
            ),
            {
                "id": backup.use_case_id,
                "spec": json.dumps(backup.spec),
                "columns": json.dumps(columns),
            },
        )
    conn.execute(
        sa.text(
            "UPDATE example_use_cases SET status = 'needs_rereview', review_note = :note "
            "WHERE spec_version = '1'"
        ),
        {"note": V1_ONLY_NOTE},
    )
    op.drop_table("spec_draft0_backup")
    op.drop_index("ix_prepared_cohorts_expires_at", table_name="prepared_cohorts")
    op.drop_index("ix_prepared_cohorts_created_by", table_name="prepared_cohorts")
    op.drop_table("prepared_cohorts")
    op.drop_index("ix_cohort_interpretations_expires_at", table_name="cohort_interpretations")
    op.drop_index("ix_cohort_interpretations_created_by", table_name="cohort_interpretations")
    op.drop_table("cohort_interpretations")
