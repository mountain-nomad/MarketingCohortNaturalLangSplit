"""Crawler settings, loaded from ``COHORTSPLIT_CRAWLER_*`` environment variables.

List settings are comma-separated, e.g. ``COHORTSPLIT_CRAWLER_SCHEMAS=public,sales``.
"""

import re
from typing import Annotated

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from cohortsplit.config import ConfigError
from cohortsplit.crawler.sampling import DEFAULT_SAMPLE_DENYLIST, SamplingPolicy

ENV_PREFIX = "COHORTSPLIT_CRAWLER_"

__all__ = ["DEFAULT_SAMPLE_DENYLIST", "CrawlerSettings", "load_crawler_settings"]


_QUALIFIED_TABLE = re.compile(r"^[^.\s]+\.[^.\s]+$")

CommaList = Annotated[tuple[str, ...], NoDecode]


class CrawlerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")

    # Master switch: when false no sample values or key examples are read at all.
    sampling_enabled: bool = True
    # A column is "low-cardinality" if it has at most this many distinct values.
    sample_max_distinct: int = Field(default=50, ge=1, le=1000)
    # Extra column globs never sampled ("col", "table.col" or "schema.table.col").
    sample_denylist: CommaList = ()
    # Schemas to crawl; empty = every non-system schema the role may use.
    schemas: CommaList = ()
    # The user entity table as "schema.table"; detected by name when unset.
    user_table: str | None = None

    @field_validator("sample_denylist", "schemas", mode="before")
    @classmethod
    def _split_commas(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("user_table", mode="before")
    @classmethod
    def _qualified_table(cls, value: object) -> object:
        if value in ("", None):
            return None
        if not isinstance(value, str) or not _QUALIFIED_TABLE.match(value):
            raise ValueError('must be "schema.table"')
        return value

    def effective_denylist(self) -> tuple[str, ...]:
        return (*DEFAULT_SAMPLE_DENYLIST, *self.sample_denylist)

    def sampling_policy(
        self, export_granted_columns: frozenset[str] = frozenset()
    ) -> SamplingPolicy:
        return SamplingPolicy(
            enabled=self.sampling_enabled,
            denylist=self.effective_denylist(),
            export_granted_columns=export_granted_columns,
            max_distinct=self.sample_max_distinct,
        )


def load_crawler_settings() -> CrawlerSettings:
    """Load crawler settings or raise :class:`ConfigError` naming the bad variables."""
    try:
        return CrawlerSettings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{ENV_PREFIX}{str(err['loc'][0]).upper() if err['loc'] else '<unknown>'}: "
            f"{err['msg']}"
            for err in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigError(f"Invalid crawler configuration: {problems}") from None
