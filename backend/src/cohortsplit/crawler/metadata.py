"""Collect schema documentation and policy-bounded data profiles from a warehouse."""

from collections.abc import Sequence

from cohortsplit.crawler.catalog import WarehouseCatalog
from cohortsplit.crawler.sampling import SamplingPolicy
from cohortsplit.warehouse.adapter import WarehouseAdapter


def collect_catalog(
    adapter: WarehouseAdapter,
    policy: SamplingPolicy,
    *,
    schemas: Sequence[str] = (),
    user_table: str | None = None,
) -> WarehouseCatalog:
    """Discover tables/columns/keys/row counts and sample permitted low-cardinality columns.

    ``schemas`` limits the crawl (empty = every schema the role may use).
    ``user_table`` is the configured user entity table ("schema.table"); when ``None``
    it is detected by name. Its key values are never read as examples.
    """
    raise NotImplementedError
