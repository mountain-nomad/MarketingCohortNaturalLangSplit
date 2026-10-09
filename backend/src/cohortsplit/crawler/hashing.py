"""Content hash of generated crawler output."""

from cohortsplit.crawler.catalog import WarehouseCatalog
from cohortsplit.crawler.use_cases import UseCaseGeneration


def content_hash(catalog: WarehouseCatalog, generation: UseCaseGeneration) -> str:
    """SHA-256 (hex) over canonical JSON of the generated content. No timestamps or ids,
    so it changes only when the generated content changes."""
    raise NotImplementedError
