"""Content hash of generated crawler output.

The semantic-context branch derives the semantic version from this hash plus the
human-authored content; it therefore changes only when generated content changes.
"""

import hashlib
import json
from typing import Any

from cohortsplit.crawler.catalog import WarehouseCatalog
from cohortsplit.crawler.use_cases import UseCaseGeneration

HASH_FORMAT = 1


def generated_content(catalog: WarehouseCatalog, generation: UseCaseGeneration) -> dict[str, Any]:
    """Canonical, JSON-ready view of everything a crawl generates (no ids, no timestamps)."""
    return {
        "format": HASH_FORMAT,
        "dialect": catalog.dialect,
        "tables": [t.model_dump(mode="json") for t in catalog.tables],
        "profiles": [p.model_dump(mode="json") for p in catalog.profiles],
        "use_cases": [
            {
                "key": u.key,
                "template_key": u.template_key,
                "nl_request": u.nl_request,
                "spec": u.spec.model_dump(mode="json"),
                "rewritten_from": u.rewritten_from,
                "generation_note": u.generation_note,
            }
            for u in generation.use_cases
        ],
        "dropped": [
            {"template_key": d.template_key, "nl_template": d.nl_template, "reason": d.reason}
            for d in generation.dropped
        ],
    }


def content_hash(catalog: WarehouseCatalog, generation: UseCaseGeneration) -> str:
    """SHA-256 (hex) over canonical JSON of the generated content. No timestamps or ids,
    so it changes only when the generated content changes."""
    canonical = json.dumps(
        generated_content(catalog, generation),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
