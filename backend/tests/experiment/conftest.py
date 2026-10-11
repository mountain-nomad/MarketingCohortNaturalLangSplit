"""Experiment split API tests reuse the auth fixtures; every test runs on SQLite and PostgreSQL.

The semantic snapshot, the LLM and the warehouse executor are fakes injected through
``get_cohort_environment`` (``tests.cohort.helpers``). End-to-end tests against the demo
warehouse live in ``tests/cohort_reference``.
"""

from tests.auth.conftest import *  # noqa: F403  (shared auth fixtures)
