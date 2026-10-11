"""End-to-end cohort tests against the demo ``ecommerce`` warehouse (integration).

PostgreSQL appdb with the real semantic provider, the real crawl and the real read-only
executor; only the LLM is a deterministic fake that returns the spec a correct
interpreter would produce.
"""

from tests.semantic.conftest import *  # noqa: F403  (PG engine, auth fixtures, warehouse DSNs)
