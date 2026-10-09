"""Authentication, RBAC, export-column grants, sessions, login throttling, audit log.

Revision ID: 0002_auth
Revises: 0003_warehouse_metadata
Create Date: 2026-10-09

Re-chained after 0003_warehouse_metadata when feature/warehouse-crawler merged first;
the revision id keeps its original name (ids are opaque; renaming would break
databases already stamped 0002_auth during development).

Seeds the permission catalog (FR-A3) and the protected Admin system role. Admin holds
every permission implicitly (no role_permissions rows), including future ones.
``audit_events`` is append-only at the database level (UPDATE/DELETE/TRUNCATE raise).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_auth"
down_revision: str | None = "0003_warehouse_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Snapshot of cohortsplit.auth.catalog.PERMISSION_CATALOG at this revision (migrations
# must not import application code). An integration test checks they match.
PERMISSIONS = (
    ("user.read", "List and view users"),
    ("user.create", "Create users"),
    ("user.update", "Edit users"),
    ("user.deactivate", "Deactivate and reactivate users"),
    ("user.reset_password", "Reset user passwords"),
    ("role.read", "List and view roles"),
    ("role.create", "Create roles"),
    ("role.update", "Edit roles, permissions and export grants"),
    ("role.delete", "Delete roles"),
    ("role.assign", "Assign roles to users"),
    ("semantic_context.read", "View business context and docs"),
    ("semantic_context.edit", "Edit business context"),
    ("use_case.review", "Review generated use cases"),
    ("crawler.run", "Run the warehouse crawler"),
    ("cohort.create", "Interpret, preview and split cohorts"),
    ("cohort.export", "Download and re-download cohort exports"),
    ("cohort.read_all", "See everyone's cohort runs"),
    ("audit.read", "Read the audit log"),
)


def _id() -> sa.Column[int]:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False)


def _ts(name: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "users",
        _id(),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "must_change_password", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("last_login_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
        sa.CheckConstraint("length(email) BETWEEN 3 AND 254", name="ck_users_email_length"),
        sa.CheckConstraint(
            "length(display_name) BETWEEN 1 AND 200", name="ck_users_display_name_length"
        ),
    )

    op.create_table(
        "roles",
        _id(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 100", name="ck_roles_name_length"),
    )
    op.create_index("uq_roles_name_lower", "roles", [sa.text("lower(name)")], unique=True)
    op.create_index(
        "uq_roles_single_system_role",
        "roles",
        ["is_system"],
        unique=True,
        postgresql_where=sa.text("is_system"),
    )

    op.create_table(
        "permissions",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_permissions"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("permission_key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_role_permissions_role_id_roles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["permission_key"],
            ["permissions.key"],
            name="fk_role_permissions_permission_key_permissions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_key", name="pk_role_permissions"),
    )
    op.create_index("ix_role_permissions_permission_key", "role_permissions", ["permission_key"])

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_roles_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_user_roles_role_id_roles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
    )
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])

    op.create_table(
        "role_export_columns",
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("column_ref", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_role_export_columns_role_id_roles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "column_ref", name="pk_role_export_columns"),
    )

    op.create_table(
        "auth_sessions",
        _id(),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("csrf_token_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        _ts("created_at"),
        _ts("last_seen_at"),
        _ts("expires_at"),
        _ts("revoked_at", nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_auth_sessions_user_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "login_throttles",
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("window_started_at", nullable=True),
        _ts("locked_until", nullable=True),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("email", name="pk_login_throttles"),
    )

    op.create_table(
        "audit_events",
        _id(),
        _ts("occurred_at"),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_events_actor_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.CheckConstraint(
            "actor_type IN ('user', 'cli', 'anonymous')", name="ck_audit_events_actor_type"
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'denied', 'error')", name="ck_audit_events_outcome"
        ),
        sa.CheckConstraint(
            "(actor_type = 'user') = (actor_user_id IS NOT NULL)",
            name="ck_audit_events_actor_user_matches_type",
        ),
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])

    op.execute(
        """
        CREATE FUNCTION audit_events_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% refused)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()"
    )

    permissions = sa.table(
        "permissions", sa.column("key", sa.Text()), sa.column("description", sa.Text())
    )
    op.bulk_insert(permissions, [{"key": k, "description": d} for k, d in PERMISSIONS])
    op.execute(
        "INSERT INTO roles (name, description, is_system, created_at, updated_at) "
        "VALUES ('Admin', 'Protected system role with every permission', true, now(), now())"
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
    op.drop_table("login_throttles")
    op.drop_table("auth_sessions")
    op.drop_table("role_export_columns")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("users")
