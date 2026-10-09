"""Application settings loaded from ``COHORTSPLIT_*`` environment variables."""

from pydantic_settings import BaseSettings


class ConfigError(Exception):
    """Raised when settings are missing or invalid."""


class Settings(BaseSettings):
    pass


def load_settings() -> Settings:
    raise NotImplementedError
