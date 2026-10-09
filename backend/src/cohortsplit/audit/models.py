"""Append-only audit event table (FR-A5).

The application never updates or deletes rows; in PostgreSQL triggers from migration
``0002_auth`` also refuse UPDATE, DELETE and TRUNCATE.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Identity, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from cohortsplit.orm import Base, BigId, JsonDocument, UTCDateTime

ACTOR_TYPES = ("user", "cli", "anonymous")
OUTCOMES = ("success", "denied", "error")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("actor_type IN ('user', 'cli', 'anonymous')", name="actor_type"),
        CheckConstraint("outcome IN ('success', 'denied', 'error')", name="outcome"),
        CheckConstraint(
            "(actor_type = 'user') = (actor_user_id IS NOT NULL)", name="actor_user_matches_type"
        ),
    )

    id: Mapped[int] = mapped_column(BigId, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        BigId, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonDocument, nullable=False, server_default=text("'{}'")
    )
