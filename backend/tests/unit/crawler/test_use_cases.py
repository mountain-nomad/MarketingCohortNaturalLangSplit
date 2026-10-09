"""Data-driven example use cases from deterministic templates (AC-22, AC-23)."""

from cohortsplit.cohort_spec.draft import (
    AttributeCondition,
    AttributeTimeWindowCondition,
    RelatedCondition,
    referenced_columns,
)
from cohortsplit.crawler.catalog import WarehouseCatalog
from cohortsplit.crawler.metadata import collect_catalog
from cohortsplit.crawler.sampling import DEFAULT_SAMPLE_DENYLIST, SamplingPolicy
from cohortsplit.crawler.use_cases import GeneratedUseCase, UseCaseGeneration, generate_use_cases
from tests.fakes import FakeAdapter, shop_tables


def _catalog(*, sampling: bool = True, **shop: bool) -> WarehouseCatalog:
    policy = SamplingPolicy(enabled=sampling, denylist=DEFAULT_SAMPLE_DENYLIST)
    return collect_catalog(FakeAdapter(shop_tables(**shop)), policy)


def _by_template(result: UseCaseGeneration, key: str) -> GeneratedUseCase:
    matches = [u for u in result.use_cases if u.template_key == key]
    assert len(matches) == 1, [u.template_key for u in result.use_cases]
    return matches[0]


def _related(use_case: GeneratedUseCase) -> RelatedCondition:
    assert isinstance(use_case.spec.where, RelatedCondition), use_case.spec.where
    return use_case.spec.where


def _path_tables(condition: RelatedCondition) -> list[str]:
    return [j.to_column.rsplit(".", 1)[0] for j in condition.path]


def test_liked_product_rewritten_to_bought_product_without_likes_data() -> None:
    result = generate_use_cases(_catalog())

    liked = _by_template(result, "liked_product")
    assert liked.status == "pending_review"
    assert liked.nl_request == "Users who bought product 12"
    assert liked.rewritten_from is not None
    assert "liked product" in liked.rewritten_from
    assert liked.generation_note is not None
    assert "likes" in liked.generation_note
    condition = _related(liked)
    assert _path_tables(condition) == [
        "public.orders",
        "public.order_items",
        "public.product_variants",
        "public.products",
    ]
    assert [(f.column, f.operator, f.value) for f in condition.filters] == [
        ("public.products.product_id", "=", "12")
    ]


def test_liked_product_kept_when_likes_data_exists() -> None:
    result = generate_use_cases(_catalog(with_likes=True))

    liked = _by_template(result, "liked_product")
    assert liked.nl_request == "Users who liked product 12"
    assert liked.rewritten_from is None
    assert _path_tables(_related(liked)) == ["public.product_likes", "public.products"]


def test_template_dropped_with_reason() -> None:
    result = generate_use_cases(_catalog())

    dropped = {d.template_key: d for d in result.dropped}
    assert "viewed_product" in dropped
    assert "view" in dropped["viewed_product"].reason
    assert "viewed product" in dropped["viewed_product"].nl_template
    assert all(u.template_key != "viewed_product" for u in result.use_cases)


def test_referral_rewritten_to_discount_without_referral_data() -> None:
    result = generate_use_cases(_catalog())

    referral = _by_template(result, "referral_campaign")
    assert referral.nl_request == "Users who placed an order with a discount"
    assert referral.rewritten_from is not None
    assert "referral" in referral.rewritten_from
    assert referral.generation_note is not None
    assert "referral" in referral.generation_note
    condition = _related(referral)
    assert condition.filters[0].column == "public.order_items.discount"
    assert condition.filters[0].operator == ">"


def test_referral_kept_with_real_value_when_data_exists() -> None:
    result = generate_use_cases(_catalog(with_referral=True))

    referral = _by_template(result, "referral_campaign")
    assert referral.nl_request == "Users who joined through referral campaign GET100"
    assert referral.rewritten_from is None
    assert isinstance(referral.spec.where, AttributeCondition)
    assert referral.spec.where.filter.column == "public.users.referral_code"
    assert referral.spec.where.filter.value == "GET100"


def test_status_templates_use_real_values() -> None:
    result = generate_use_cases(_catalog())

    orders = _by_template(result, "orders_with_status")
    assert orders.nl_request == (
        "Users who placed at least 2 orders with status delivered in the last 90 days"
    )
    condition = _related(orders)
    assert condition.filters[0].column == "public.orders.status"
    assert condition.filters[0].value == "delivered"
    assert condition.time_window is not None
    assert condition.time_window.column == "public.orders.ordered_at"
    assert condition.time_window.last_days == 90
    assert condition.aggregate is not None
    assert (condition.aggregate.function, condition.aggregate.operator) == ("count", ">=")
    assert condition.aggregate.value == 2

    cart = _by_template(result, "cart_with_status")
    # 'abandoned' is preferred; it is a permitted value (CHECK constraint) even if not sampled.
    assert cart.nl_request == "Users who have a cart with status abandoned"
    assert _related(cart).filters[0].column == "public.carts.status"


def test_country_category_spend_and_registration() -> None:
    result = generate_use_cases(_catalog())

    country = _by_template(result, "from_country")
    assert country.nl_request == "Users from country KZ"
    assert _related(country).filters[0].column == "public.addresses.country_code"

    category = _by_template(result, "purchased_in_category")
    assert category.nl_request == "Users who bought anything from category Beverages"
    assert _related(category).filters[0].column == "public.categories.name"
    assert "public.order_items" in _path_tables(_related(category))

    spend = _by_template(result, "spent_more_than")
    aggregate = _related(spend).aggregate
    assert aggregate is not None
    assert (aggregate.function, aggregate.column, aggregate.operator, aggregate.value) == (
        "sum",
        "public.orders.grand_total",
        ">",
        500,
    )

    registered = _by_template(result, "registered_recently")
    assert registered.nl_request == "Users who registered in the last 30 days"
    assert isinstance(registered.spec.where, AttributeTimeWindowCondition)
    assert registered.spec.where.window.column == "public.users.created_at"


def test_all_generated_use_cases_pending_review_and_valid() -> None:
    catalog = _catalog()
    result = generate_use_cases(catalog)

    assert len(result.use_cases) >= 8
    assert all(u.status == "pending_review" for u in result.use_cases)
    keys = [u.key for u in result.use_cases]
    assert len(keys) == len(set(keys))
    for use_case in result.use_cases:
        assert use_case.spec.spec_version == "draft-0"
        assert use_case.spec.entity.table == "public.users"
        assert use_case.spec.entity.key == "user_id"
        missing = referenced_columns(use_case.spec) - catalog.qualified_columns()
        assert not missing, (use_case.key, missing)


def test_generation_is_deterministic() -> None:
    first = generate_use_cases(_catalog())
    second = generate_use_cases(_catalog())

    assert first == second


def test_sampling_disabled_drops_templates_needing_example_values() -> None:
    result = generate_use_cases(_catalog(sampling=False))

    dropped = {d.template_key: d.reason for d in result.dropped}
    assert "liked_product" in dropped
    assert "example value" in dropped["liked_product"]
    assert "from_country" in dropped
    # Schema metadata (CHECK constraint values) still supports status templates.
    assert _by_template(result, "cart_with_status").nl_request.endswith("abandoned")
    assert _by_template(result, "registered_recently")


def test_no_user_table_drops_everything_with_reason() -> None:
    catalog = _catalog()
    without_users = catalog.model_copy(
        update={"tables": tuple(t for t in catalog.tables if t.name != "users")}
    )

    result = generate_use_cases(without_users)

    assert result.use_cases == ()
    assert result.dropped
    assert all("user" in d.reason for d in result.dropped)


def test_configured_user_table() -> None:
    result = generate_use_cases(_catalog(), user_table="public.nope")

    assert result.use_cases == ()
    assert all("public.nope" in d.reason for d in result.dropped)
