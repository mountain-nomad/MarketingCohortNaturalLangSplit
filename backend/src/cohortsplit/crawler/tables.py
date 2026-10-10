"""appdb tables for crawler output (SQLAlchemy Core). Created by migration 0003."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

ORIGINS = ("generated", "human")
USE_CASE_STATUSES = ("pending_review", "confirmed", "rejected", "needs_rereview")
DOC_KINDS = ("table_schema", "data_profile", "note")
RUN_STATUSES = ("running", "succeeded", "failed")

crawl_runs = Table(
    "crawl_runs",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("status", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    # Actor seam: filled with the authenticated user once auth merges ("cli" until then).
    Column("triggered_by", Text),
    Column("content_hash", String(64)),
    Column("summary", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("error", Text),
    CheckConstraint(f"status IN {RUN_STATUSES}", name="crawl_runs_status_check"),
)

semantic_docs = Table(
    "semantic_docs",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("doc_key", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("origin", Text, nullable=False),
    Column("content", JSONB, nullable=False),
    Column("crawl_run_id", BigInteger, ForeignKey("crawl_runs.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(f"kind IN {DOC_KINDS}", name="semantic_docs_kind_check"),
    CheckConstraint(f"origin IN {ORIGINS}", name="semantic_docs_origin_check"),
    UniqueConstraint("doc_key", "kind", "origin", name="semantic_docs_key_kind_origin_key"),
)

example_use_cases = Table(
    "example_use_cases",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("use_case_key", Text, nullable=False),
    Column("origin", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("nl_request", Text, nullable=False),
    Column("spec", JSONB, nullable=False),
    Column("spec_version", Text, nullable=False),
    Column("template_key", Text),
    Column("rewritten_from", Text),
    Column("generation_note", Text),
    Column("review_note", Text),
    Column("referenced_columns", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("crawl_run_id", BigInteger, ForeignKey("crawl_runs.id", ondelete="SET NULL")),
    # Last review decision (migration 0004; the FK to users.id lives in the database).
    Column("reviewed_by", BigInteger),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(f"origin IN {ORIGINS}", name="example_use_cases_origin_check"),
    CheckConstraint(f"status IN {USE_CASE_STATUSES}", name="example_use_cases_status_check"),
    Index(
        "example_use_cases_generated_key_idx",
        "use_case_key",
        unique=True,
        postgresql_where=text("origin = 'generated'"),
    ),
    Index("example_use_cases_status_idx", "status"),
)
