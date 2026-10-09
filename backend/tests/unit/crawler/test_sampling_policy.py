"""Sampling policy (AC-27): fail closed for denylisted, export-granted, non-categorical columns."""

import pytest

from cohortsplit.crawler.sampling import NoExportGrants, SamplingPolicy, StaticExportGrants
from cohortsplit.crawler.settings import DEFAULT_SAMPLE_DENYLIST
from cohortsplit.warehouse.models import TableRef, TypeCategory
from tests.fakes import col

USERS = TableRef(schema="public", name="users")
ORDERS = TableRef(schema="public", name="orders")


def _policy(**kwargs: object) -> SamplingPolicy:
    params: dict[str, object] = {"enabled": True, "denylist": DEFAULT_SAMPLE_DENYLIST}
    params.update(kwargs)
    return SamplingPolicy(**params)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name",
    [
        "email",
        "EMAIL",
        "contact_email",
        "phone",
        "mobile_phone",
        "password_hash",
        "password",
        "session_token",
        "api_token_id",
        "address",
        "address_line1",
        "client_secret",
        "first_name",
        "last_name",
        "line1",
        "ship_line2",
        "postal_code",
        "ship_name",
    ],
)
def test_default_denylist_blocks(name: str) -> None:
    decision = _policy().decide(USERS, col(name))

    assert not decision.allowed
    assert "denylist" in decision.reason


@pytest.mark.parametrize("name", ["status", "country_code", "currency", "provider", "is_active"])
def test_categorical_columns_allowed(name: str) -> None:
    category: TypeCategory = "boolean" if name == "is_active" else "string"

    decision = _policy().decide(ORDERS, col(name, category))

    assert decision.allowed, decision.reason


def test_enum_allowed() -> None:
    assert _policy().decide(ORDERS, col("mood", "enum")).allowed


@pytest.mark.parametrize("category", ["numeric", "datetime", "other"])
def test_non_categorical_types_not_sampled(category: TypeCategory) -> None:
    decision = _policy().decide(ORDERS, col("x", category))

    assert not decision.allowed
    assert "type" in decision.reason


def test_disabled_sampling_blocks_everything() -> None:
    policy = _policy(enabled=False)

    assert not policy.enabled
    decision = policy.decide(ORDERS, col("status"))
    assert not decision.allowed
    assert "disabled" in decision.reason
    assert not policy.decide_key_example(ORDERS, col("order_id", "numeric")).allowed


def test_extra_patterns_extend_and_qualified_patterns_match() -> None:
    policy = _policy(denylist=(*DEFAULT_SAMPLE_DENYLIST, "public.orders.ship_city", "carts.note"))

    assert not policy.decide(ORDERS, col("ship_city")).allowed
    assert policy.decide(USERS, col("ship_city")).allowed
    assert not policy.decide(TableRef(schema="sales", name="carts"), col("note")).allowed
    assert not policy.decide(USERS, col("email")).allowed


def test_empty_denylist_is_not_fail_open_for_defaults() -> None:
    """Defaults always apply: a policy built with no patterns still blocks `email`."""
    policy = SamplingPolicy(enabled=True, denylist=())

    assert not policy.decide(USERS, col("email")).allowed
    assert not policy.decide(USERS, col("password_hash")).allowed


@pytest.mark.parametrize("granted", ["public.users.first_nick", "users.first_nick"])
def test_export_granted_columns_never_sampled(granted: str) -> None:
    policy = _policy(export_granted_columns=StaticExportGrants([granted]).export_granted_columns())

    decision = policy.decide(USERS, col("first_nick"))

    assert not decision.allowed
    assert "export" in decision.reason
    assert not policy.decide_key_example(USERS, col("first_nick")).allowed
    assert policy.decide(ORDERS, col("first_nick")).allowed


def test_no_export_grants_default_is_empty() -> None:
    assert NoExportGrants().export_granted_columns() == frozenset()


def test_key_examples_allowed_for_numeric_keys_but_respect_denylist() -> None:
    policy = _policy()

    assert policy.decide_key_example(ORDERS, col("order_id", "numeric")).allowed
    assert not policy.decide_key_example(USERS, col("email")).allowed
    assert not policy.decide_key_example(ORDERS, col("ordered_at", "datetime")).allowed


def test_max_distinct_validated() -> None:
    with pytest.raises(ValueError, match="max_distinct"):
        _policy(max_distinct=0)
    assert _policy(max_distinct=7).max_distinct == 7
