"""Semantic context: business-context entries, semantic version history, use-case reviewer.

Revision ID: 0004_semantic_context
Revises: 0002_auth
Create Date: 2026-10-10

* ``business_context_entries`` — human-authored, typed business definitions (FR-3).
* ``semantic_versions`` — append-only history of semantic versions with actor and cause.
* ``example_use_cases.reviewed_by`` / ``reviewed_at`` — who last decided on a use case.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_semantic_context"
down_revision: str | None = "0002_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "('term', 'metric', 'status_semantics', 'time_window', 'exclusion', 'canonical_user_id')"


def _ts(name: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "business_context_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("synonyms", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_business_context_entries"),
        sa.UniqueConstraint("key", name="uq_business_context_entries_key"),
        sa.CheckConstraint(f"kind IN {KINDS}", name="ck_business_context_entries_kind"),
        sa.CheckConstraint(
            "key ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_business_context_entries_key_format"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_business_context_entries_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_business_context_entries_updated_by_users",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "semantic_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("components", postgresql.JSONB(), nullable=False),
        sa.Column("cause", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_versions"),
        sa.CheckConstraint("actor_type IN ('user', 'cli')", name="ck_semantic_versions_actor_type"),
        sa.CheckConstraint(
            "(actor_type = 'user') = (actor_user_id IS NOT NULL)",
            name="ck_semantic_versions_actor_user_matches_type",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_semantic_versions_actor_user_id_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_semantic_versions_created_at", "semantic_versions", ["created_at"])

    op.add_column("example_use_cases", sa.Column("reviewed_by", sa.BigInteger(), nullable=True))
    op.add_column("example_use_cases", _ts("reviewed_at", nullable=True))
    op.create_foreign_key(
        "example_use_cases_reviewed_by_fkey",
        "example_use_cases",
        "users",
        ["reviewed_by"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "example_use_cases_reviewed_by_fkey", "example_use_cases", type_="foreignkey"
    )
    op.drop_column("example_use_cases", "reviewed_at")
    op.drop_column("example_use_cases", "reviewed_by")
    op.drop_index("ix_semantic_versions_created_at", table_name="semantic_versions")
    op.drop_table("semantic_versions")
    op.drop_table("business_context_entries")
