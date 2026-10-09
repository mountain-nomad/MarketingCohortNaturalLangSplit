"""Crawler settings from ``COHORTSPLIT_CRAWLER_*``."""

import pytest

from cohortsplit.config import ConfigError
from cohortsplit.crawler.sampling import SamplingPolicy
from cohortsplit.crawler.settings import DEFAULT_SAMPLE_DENYLIST, load_crawler_settings
from cohortsplit.warehouse.models import TableRef
from tests.fakes import col


def test_defaults(clean_env: pytest.MonkeyPatch) -> None:
    settings = load_crawler_settings()

    assert settings.sampling_enabled is True
    assert settings.sample_max_distinct == 50
    assert settings.sample_denylist == ()
    assert settings.schemas == ()
    assert settings.user_table is None
    assert settings.effective_denylist() == DEFAULT_SAMPLE_DENYLIST


def test_env_overrides(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("COHORTSPLIT_CRAWLER_SAMPLING_ENABLED", "false")
    clean_env.setenv("COHORTSPLIT_CRAWLER_SAMPLE_MAX_DISTINCT", "12")
    clean_env.setenv("COHORTSPLIT_CRAWLER_SAMPLE_DENYLIST", "ship_city, *.notes ,")
    clean_env.setenv("COHORTSPLIT_CRAWLER_SCHEMAS", "public,sales")
    clean_env.setenv("COHORTSPLIT_CRAWLER_USER_TABLE", "public.customers")

    settings = load_crawler_settings()

    assert settings.sampling_enabled is False
    assert settings.sample_max_distinct == 12
    assert settings.sample_denylist == ("ship_city", "*.notes")
    assert settings.schemas == ("public", "sales")
    assert settings.user_table == "public.customers"
    assert settings.effective_denylist() == (*DEFAULT_SAMPLE_DENYLIST, "ship_city", "*.notes")


def test_policy_from_settings(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("COHORTSPLIT_CRAWLER_SAMPLE_DENYLIST", "status")
    settings = load_crawler_settings()

    policy = settings.sampling_policy(frozenset({"public.users.country"}))

    assert isinstance(policy, SamplingPolicy)
    users = TableRef(schema="public", name="users")
    assert not policy.decide(users, col("status")).allowed
    assert not policy.decide(users, col("country")).allowed
    assert not policy.decide(users, col("email")).allowed


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("COHORTSPLIT_CRAWLER_SAMPLE_MAX_DISTINCT", "0"),
        ("COHORTSPLIT_CRAWLER_SAMPLE_MAX_DISTINCT", "100000"),
        ("COHORTSPLIT_CRAWLER_USER_TABLE", "users"),
        ("COHORTSPLIT_CRAWLER_SAMPLING_ENABLED", "maybe"),
    ],
)
def test_invalid_values_name_the_variable(
    clean_env: pytest.MonkeyPatch, name: str, value: str
) -> None:
    clean_env.setenv(name, value)

    with pytest.raises(ConfigError, match=name):
        load_crawler_settings()
