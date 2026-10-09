"""The content hash changes only when generated content changes (AC-25 version input)."""

from cohortsplit.crawler.hashing import content_hash
from cohortsplit.crawler.metadata import collect_catalog
from cohortsplit.crawler.sampling import DEFAULT_SAMPLE_DENYLIST, SamplingPolicy
from cohortsplit.crawler.use_cases import generate_use_cases
from tests.fakes import FakeAdapter, col, shop_tables


def _hash(**shop: bool) -> str:
    adapter = FakeAdapter(shop_tables(**shop))
    catalog = collect_catalog(
        adapter, SamplingPolicy(enabled=True, denylist=DEFAULT_SAMPLE_DENYLIST)
    )
    return content_hash(catalog, generate_use_cases(catalog))


def test_hash_is_hex_sha256_and_stable() -> None:
    first = _hash()

    assert len(first) == 64
    assert int(first, 16) >= 0
    assert first == _hash()


def test_hash_changes_with_schema() -> None:
    assert _hash() != _hash(with_likes=True)


def test_hash_changes_with_sampled_values() -> None:
    tables = shop_tables()
    base = FakeAdapter(tables)
    policy = SamplingPolicy(enabled=True, denylist=DEFAULT_SAMPLE_DENYLIST)
    catalog = collect_catalog(base, policy)
    before = content_hash(catalog, generate_use_cases(catalog))

    changed = shop_tables()
    orders = next(t for t in changed if t.ref.name == "orders")
    orders.values["status"] = ["delivered", "paid"]
    orders.columns.append(col("note"))
    catalog2 = collect_catalog(FakeAdapter(changed), policy)

    assert content_hash(catalog2, generate_use_cases(catalog2)) != before
