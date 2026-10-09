"""ORM models for business context and the semantic version history.

Must stay in sync with Alembic revision ``0004_semantic_context`` (an integration test
compares them). The key-format CHECK (a PostgreSQL regex) lives in the migration only;
the application validates keys before writing.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Identity, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from cohortsplit.orm import Base, BigId, JsonDocument, UTCDateTime

ENTRY_KINDS_SQL = (
    "('term', 'metric', 'status_semantics', 'time_window', 'exclusion', 'canonical_user_id')"
)


class BusinessContextEntry(Base):
    __tablename__ = "business_context_entries"
    __table_args__ = (CheckConstraint(f"kind IN {ENTRY_KINDS_SQL}", name="kind"),)

    id: Mapped[int] = mapped_column(BigId, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    synonyms: Mapped[list[str]] = mapped_column(
        JsonDocument, nullable=False, server_default=text("'[]'")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    definition: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        BigId, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    updated_by: Mapped[int | None] = mapped_column(
        BigId, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class SemanticVersionRecord(Base):
    """One row per semantic-version change (append-only by convention)."""

    __tablename__ = "semantic_versions"
    __table_args__ = (
        CheckConstraint("actor_type IN ('user', 'cli')", name="actor_type"),
        CheckConstraint(
            "(actor_type = 'user') = (actor_user_id IS NOT NULL)", name="actor_user_matches_type"
        ),
    )

    id: Mapped[int] = mapped_column(BigId, Identity(always=True), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    components: Mapped[dict[str, str]] = mapped_column(JsonDocument, nullable=False)
    cause: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        BigId, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
