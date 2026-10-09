"""Crawls triggered through the API. STUB: implemented in the GREEN commit."""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.warehouse.adapter import WarehouseAdapter

CRAWL_RUN_LOCK_KEY = 0x4352554E  # "CRUN"


@dataclass(frozen=True)
class CrawlEnvironment:
    adapter_factory: Callable[[], WarehouseAdapter]
    crawler_settings: Callable[[], CrawlerSettings]


def get_crawl_environment(request: Request) -> CrawlEnvironment:
    raise NotImplementedError
