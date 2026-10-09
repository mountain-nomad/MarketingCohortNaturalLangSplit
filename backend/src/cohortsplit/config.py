"""Application settings loaded from ``COHORTSPLIT_*`` environment variables.

Secrets are held as :class:`pydantic.SecretStr` so they never appear in
``repr``/``str`` output or logs.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "COHORTSPLIT_"


class ConfigError(Exception):
    """Raised when settings are missing or invalid. The message names the env vars."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")

    appdb_host: str = "localhost"
    appdb_port: int = Field(default=5432, ge=1, le=65535)
    appdb_name: str = "cohortsplit"
    appdb_user: str = "cohortsplit"
    appdb_password: SecretStr

    # Read-only warehouse connection (libpq URL, password URL-encoded). Optional until
    # the warehouse adapter lands; never logged.
    warehouse_dsn: SecretStr | None = None
    # Limits enforced by the read-only warehouse executor (FR-6).
    warehouse_statement_timeout_seconds: float = Field(default=30.0, gt=0)
    warehouse_row_cap: int = Field(default=1_000_000, ge=1)
    warehouse_connect_timeout_seconds: int = Field(default=5, ge=1)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Directory with the built frontend (index.html); when unset, no UI is served.
    frontend_dist: Path | None = None

    # --- Authentication (docs/product/authentication.md, resolved decision 1) ---
    session_idle_timeout_minutes: int = Field(default=480, ge=1)
    session_max_lifetime_hours: int = Field(default=168, ge=1)
    login_max_failures: int = Field(default=5, ge=1)
    login_failure_window_minutes: int = Field(default=15, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)
    # Session cookie `Secure` flag: true/false, or unset = auto (Secure when served over HTTPS).
    cookie_secure: bool | None = None
    # Interactive API docs (/api/docs, /api/openapi.json); when enabled they require a session.
    api_docs_enabled: bool = False

    @field_validator("appdb_password")
    @classmethod
    def _secret_not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("must not be empty")
        return value

    @field_validator("frontend_dist")
    @classmethod
    def _dist_has_index(cls, value: Path | None) -> Path | None:
        if value is not None and not (value / "index.html").is_file():
            raise ValueError("must be a directory containing index.html")
        return value

    @field_validator("warehouse_dsn", mode="before")
    @classmethod
    def _empty_dsn_is_unset(cls, value: object) -> object:
        return None if value == "" else value


def _env_name(loc: tuple[int | str, ...]) -> str:
    field = str(loc[0]) if loc else "<unknown>"
    return f"{ENV_PREFIX}{field.upper()}"


def load_settings() -> Settings:
    """Load settings from the environment or raise :class:`ConfigError`."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{_env_name(err['loc'])}: {err['msg']}"
            for err in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigError(f"Invalid configuration: {problems}") from None
