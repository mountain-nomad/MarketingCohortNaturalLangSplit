"""Cohort API tests reuse the auth fixtures; every test runs on SQLite and PostgreSQL.

The semantic snapshot, the LLM and the warehouse executor are injected through
``get_cohort_environment`` (deterministic fakes, no network). Integration tests with the
real provider, crawl and warehouse live in ``tests/integration``.
"""

from tests.auth.conftest import *  # noqa: F403  (shared auth fixtures)
