"""ORM models for identity, RBAC, export-column grants, sessions and login throttling.

Must stay in sync with Alembic revision ``0002_auth`` (checked by an integration test).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cohortsplit.orm import Base, BigId, UTCDateTime


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("length(email) BETWEEN 3 AND 254", name="email_length"),
        CheckConstraint("length(display_name) BETWEEN 1 AND 200", name="display_name_length"),
    )

    id: Mapped[int] = mapped_column(BigId, Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    roles: Mapped[list["Role"]] = relationship(
        secondary="user_roles", back_populates="users", order_by="Role.name"
    )

    def __repr__(self) -> str:  # never include the password hash
        return f"User(id={self.id!r}, active={self.is_active!r})"


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 1 AND 100", name="name_length"),
        Index("uq_roles_name_lower", func.lower(text("name")), unique=True),
        Index(
            "uq_roles_single_system_role",
            "is_system",
            unique=True,
            postgresql_where=text("is_system"),
            sqlite_where=text("is_system"),
        ),
    )

    id: Mapped[int] = mapped_column(BigId, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    users: Mapped[list[User]] = relationship(secondary="user_roles", back_populates="roles")
    permissions: Mapped[list["RolePermission"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    export_columns: Mapped[list["RoleExportColumn"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"Role(id={self.id!r}, name={self.name!r}, system={self.is_system!r})"


class Permission(Base):
    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(
        BigId, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_key: Mapped[str] = mapped_column(
        Text, ForeignKey("permissions.key", ondelete="RESTRICT"), primary_key=True, index=True
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        BigId, ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        BigId, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class RoleExportColumn(Base):
    """A role-attached data policy: the role may export this ``schema.table.column``."""

    __tablename__ = "role_export_columns"

    role_id: Mapped[int] = mapped_column(
        BigId, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    column_ref: Mapped[str] = mapped_column(Text, primary_key=True)


class AuthSession(Base):
    """Server-side session. Only SHA-256 digests of the session and CSRF tokens are stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(BigId, Identity(always=True), primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigId, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    def __repr__(self) -> str:  # never include token digests
        return f"AuthSession(id={self.id!r}, user_id={self.user_id!r})"


class LoginThrottle(Base):
    """Failed-login tracking per normalized email (known or unknown accounts alike)."""

    __tablename__ = "login_throttles"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    window_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
