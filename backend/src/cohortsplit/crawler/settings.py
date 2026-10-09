"""Crawler settings, loaded from ``COHORTSPLIT_CRAWLER_*`` environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "COHORTSPLIT_CRAWLER_"

# Column-name globs (case-insensitive) that are never sampled. Configured patterns
# EXTEND this list; the built-in defaults cannot be removed by configuration.
DEFAULT_SAMPLE_DENYLIST: tuple[str, ...] = (
    "email",
    "*email*",
    "phone",
    "*phone*",
    "password*",
    "*password*",
    "*token*",
    "address*",
    "*secret*",
    "*_hash",
    "first_name",
    "last_name",
    "*line1",
    "*line2",
    "*postal_code*",
    "ship_name",
)


class CrawlerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")


def load_crawler_settings() -> CrawlerSettings:
    raise NotImplementedError
