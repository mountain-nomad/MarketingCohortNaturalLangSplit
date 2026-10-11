"""Deterministic pre-LLM guards (BR-3, BR-4, BR-6, AC-8, AC-9, ruling C7).

They reject regardless of what the LLM would answer; the spec model and the SQL
validator remain independent backstops.
"""

import pytest

from cohortsplit.cohort.guards import pre_guard


@pytest.mark.parametrize(
    "request_text",
    [
        "Delete inactive users",
        "delete all users from Germany",
        "Please remove users who never ordered",
        "Can you deactivate dormant accounts?",
        "DROP TABLE users",
        "Update users set is_active = false",
        "Truncate orders",
        "Users from Germany; DELETE FROM users",
        "Grant admin to marketers",
        "Insert into users values (1)",
        "Ban users from Kazakhstan",
        "Mark all abandoned carts as converted",
    ],
)
def test_mutation_requests_are_rejected(request_text: str) -> None:
    result = pre_guard(request_text)

    assert result is not None
    assert result.reason == "mutation"
    assert "read-only" in result.message


@pytest.mark.parametrize(
    "request_text",
    [
        "What was revenue last month?",
        "How many users bought product 7?",
        "how much did we sell in 2024",
        "Compare sales by country",
        "Calculate the average order value",
        "Why did orders drop in May?",
        "Is revenue up this year?",
    ],
)
def test_analytics_questions_are_rejected(request_text: str) -> None:
    result = pre_guard(request_text)

    assert result is not None
    assert result.reason == "analytics"
    assert "cohort" in result.message.lower()


@pytest.mark.parametrize(
    "request_text",
    [
        "Users who added to cart, then didn't buy within 24h",
        "Users who signed up followed by a purchase",
        "Users who bought within 7 days after signing up",
        "Users who abandoned a cart and afterwards bought something",
        "Users from Germany who registered and then ordered",
    ],
)
def test_sequence_wording_is_not_supported_yet(request_text: str) -> None:
    result = pre_guard(request_text)

    assert result is not None
    assert result.reason == "unsupported_construct"
    assert "not supported yet" in result.message


@pytest.mark.parametrize(
    "request_text",
    [
        "Users from Kazakhstan who abandoned a cart and never paid for an order",
        "Users who placed at least 2 delivered orders in the last 90 days",
        "Users who bought anything from category Beverages but not product 7",
        "Active users who registered in 2024 and spent more than $500 in total",
        "Users who bought product 7 or used a discount",
        "Users who removed items from their cart",
        "Users who updated their profile recently",
        "Show me users from Germany",
        "Which users abandoned a cart?",
        "Our best customers",
        "Users who spent more than the average",
        "Users who deleted their account but still have open orders",
    ],
)
def test_cohort_requests_pass(request_text: str) -> None:
    assert pre_guard(request_text) is None


@pytest.mark.parametrize(
    "request_text",
    [
        "Create a cohort of users from Germany",
        "Add users who bought shoes to a cohort",
        "Build an audience of users with an abandoned cart",
        "Make a segment of active users",
        "Has a paid order",
        "Did not order in 2024",
        "Total spend above 500 in 2024",
        "Is active and registered in 2024",
    ],
)
def test_natural_cohort_phrasing_is_not_rejected(request_text: str) -> None:
    """Review finding 2: 'Create a cohort of …' is the most likely phrasing in a tab named
    'New cohort'; auxiliary-verb openers are only questions with a question mark."""
    assert pre_guard(request_text) is None


@pytest.mark.parametrize(
    "request_text",
    ["Create a table of users", "Delete the cohort list", "Did revenue grow in 2024?"],
)
def test_guard_still_rejects_near_misses(request_text: str) -> None:
    assert pre_guard(request_text) is not None
