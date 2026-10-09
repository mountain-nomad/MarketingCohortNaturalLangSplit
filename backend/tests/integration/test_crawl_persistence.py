"""Crawl persistence and re-run semantics (AC-25 crawler half, AC-26, failure behavior, BR-7).

Each test crawls a throwaway scratch schema in the demo warehouse (created with admin
rights by the fixture); the crawler itself always connects as ``cohortsplit_ro``.
"""

from collections.abc import Mapping
from typing import Any

import pytest

from cohortsplit.cohort_spec.draft import DraftCohortSpec
from cohortsplit.crawler.service import CrawlFailedError, CrawlReport, run_crawl
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.crawler.store import CrawlStore, StoredDoc, StoredUseCase
from cohortsplit.warehouse import (
    PostgresWarehouseAdapter,
    QueryResult,
    TableMetadata,
    TableRef,
    WarehouseUnavailableError,
)
from tests.integration.conftest import ScratchWarehouse

pytestmark = pytest.mark.integration


def _crawl(adapter: Any, store: CrawlStore, scratch: ScratchWarehouse) -> CrawlReport:
    settings = CrawlerSettings(schemas=(scratch.schema,))
    return run_crawl(adapter, store, settings=settings, triggered_by="test")


def _snapshot(store: CrawlStore) -> tuple[list[StoredDoc], list[StoredUseCase]]:
    return store.list_docs(), store.list_use_cases()


def _generated(store: CrawlStore, template_key: str) -> StoredUseCase:
    return next(
        u
        for u in store.list_use_cases(origin="generated")
        if u.template_key == template_key and u.status != "rejected"
    )


def _human_spec(scratch: ScratchWarehouse, column: str) -> DraftCohortSpec:
    s = scratch.schema
    return DraftCohortSpec.model_validate(
        {
            "entity": {"table": f"{s}.users", "key": "user_id"},
            "where": {
                "type": "attribute",
                "filter": {"column": f"{s}.users.{column}", "operator": "=", "value": "x"},
            },
        }
    )


class FailingAdapter:
    """Delegates to the real adapter but fails while describing ``fail_table``."""

    def __init__(self, inner: PostgresWarehouseAdapter, fail_table: str) -> None:
        self._inner = inner
        self._fail_table = fail_table
        self.dialect = inner.dialect

    def describe_location(self) -> str:
        return self._inner.describe_location()

    def test_connection(self) -> None:
        self._inner.test_connection()

    def list_schemas(self) -> list[str]:
        return self._inner.list_schemas()

    def list_tables(self, schema: str) -> list[TableRef]:
        return self._inner.list_tables(schema)

    def describe_table(self, table: TableRef) -> TableMetadata:
        if table.name == self._fail_table:
            raise WarehouseUnavailableError("Warehouse is unreachable at test:0/db.")
        return self._inner.describe_table(table)

    def get_distinct_values(
        self, table: TableRef, column: str, max_distinct: int
    ) -> list[str] | None:
        return self._inner.get_distinct_values(table, column, max_distinct)

    def get_key_examples(self, table: TableRef, column: str, limit: int) -> list[str]:
        return self._inner.get_key_examples(table, column, limit)

    def execute_readonly(self, sql: str, params: Mapping[str, object] | None = None) -> QueryResult:
        return self._inner.execute_readonly(sql, params)


def test_crawl_persists_generated_docs_and_pending_use_cases(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    report = _crawl(ro_adapter, crawl_store, scratch)

    s = scratch.schema
    tables = {f"{s}.{t}" for t in ("users", "products", "orders", "order_items", "carts")}
    docs = crawl_store.list_docs(origin="generated")
    assert {d.doc_key for d in docs if d.kind == "table_schema"} == {f"table:{t}" for t in tables}
    assert {d.doc_key for d in docs if d.kind == "data_profile"} == {f"table:{t}" for t in tables}
    orders_doc = next(
        d for d in docs if d.doc_key == f"table:{s}.orders" and d.kind == "table_schema"
    )
    assert orders_doc.content["primary_key"] == ["order_id"]
    profile = next(d for d in docs if d.doc_key == f"table:{s}.orders" and d.kind == "data_profile")
    assert profile.content["estimated_row_count"] == 20

    use_cases = crawl_store.list_use_cases()
    assert use_cases
    assert {u.origin for u in use_cases} == {"generated"}
    assert {u.status for u in use_cases} == {"pending_review"}
    assert all(u.spec["spec_version"] == "draft-0" for u in use_cases)
    assert all(u.spec_version == "draft-0" for u in use_cases)
    assert report.inserted_use_cases == len(use_cases)

    run = crawl_store.get_run(report.run_id)
    assert run is not None
    assert run.status == "succeeded"
    assert run.triggered_by == "test"
    assert run.content_hash == report.content_hash
    assert run.content_hash is not None
    assert len(run.content_hash) == 64
    assert run.finished_at is not None
    assert {d["template_key"] for d in run.summary["dropped"]} >= {"viewed_product"}


def test_generated_and_human_content_distinguishable(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    _crawl(ro_adapter, crawl_store, scratch)
    crawl_store.upsert_human_doc("business-context", "note", {"text": "purchase = paid"})
    crawl_store.add_human_use_case("Users with email x", _human_spec(scratch, "email"))

    docs = crawl_store.list_docs()
    use_cases = crawl_store.list_use_cases()

    assert {d.origin for d in docs} == {"generated", "human"}
    assert [d.doc_key for d in crawl_store.list_docs(origin="human")] == ["business-context"]
    assert {u.origin for u in use_cases} == {"generated", "human"}
    human = crawl_store.list_use_cases(origin="human")
    assert [u.nl_request for u in human] == ["Users with email x"]
    assert human[0].crawl_run_id is None
    assert f"{scratch.schema}.users.email" in human[0].referenced_columns


def test_rerun_preserves_human_docs_and_use_cases(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    _crawl(ro_adapter, crawl_store, scratch)
    crawl_store.upsert_human_doc("business-context", "note", {"text": "purchase = paid"})
    pending = crawl_store.add_human_use_case("Pending human", _human_spec(scratch, "email"))
    confirmed = crawl_store.add_human_use_case(
        "Confirmed human", _human_spec(scratch, "email"), status="confirmed"
    )
    human_docs_before = crawl_store.list_docs(origin="human")
    human_before = crawl_store.list_use_cases(origin="human")

    _crawl(ro_adapter, crawl_store, scratch)

    assert crawl_store.list_docs(origin="human") == human_docs_before
    assert crawl_store.list_use_cases(origin="human") == human_before
    assert {u.id for u in human_before} == {pending, confirmed}


def test_rerun_preserves_confirmed_and_rejected_generated_use_cases(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    _crawl(ro_adapter, crawl_store, scratch)
    confirmed = _generated(crawl_store, "orders_with_status")
    rejected = _generated(crawl_store, "spent_more_than")
    crawl_store.set_use_case_status(confirmed.id, "confirmed")
    crawl_store.set_use_case_status(rejected.id, "rejected")
    confirmed_before = next(u for u in crawl_store.list_use_cases() if u.id == confirmed.id)
    rejected_before = next(u for u in crawl_store.list_use_cases() if u.id == rejected.id)
    pending_before = {u.template_key for u in crawl_store.list_use_cases(status="pending_review")}

    report = _crawl(ro_adapter, crawl_store, scratch)

    after = crawl_store.list_use_cases()
    assert next(u for u in after if u.id == confirmed.id) == confirmed_before
    assert next(u for u in after if u.id == rejected.id) == rejected_before
    keys = [u.use_case_key for u in after if u.origin == "generated"]
    assert len(keys) == len(set(keys)), "a reviewed key must not be regenerated as pending"
    pending_after = {u.template_key for u in crawl_store.list_use_cases(status="pending_review")}
    assert pending_after == pending_before
    assert set(report.preserved_keys) == {confirmed.use_case_key, rejected.use_case_key}


def test_content_hash_stable_when_unchanged_and_changes_with_schema(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    first = _crawl(ro_adapter, crawl_store, scratch)
    second = _crawl(ro_adapter, crawl_store, scratch)

    scratch.admin("ALTER TABLE {s}.users ADD COLUMN country_code text")
    third = _crawl(ro_adapter, crawl_store, scratch)

    assert first.content_hash == second.content_hash
    assert third.content_hash != second.content_hash


def test_confirmed_use_case_flagged_when_column_removed(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    _crawl(ro_adapter, crawl_store, scratch)
    orders_status = _generated(crawl_store, "orders_with_status")
    crawl_store.set_use_case_status(orders_status.id, "confirmed")
    untouched = crawl_store.add_human_use_case(
        "Users with email x", _human_spec(scratch, "email"), status="confirmed"
    )
    flagged_human = crawl_store.add_human_use_case(
        "Users by created_at", _human_spec(scratch, "created_at"), status="confirmed"
    )

    scratch.admin("ALTER TABLE {s}.orders DROP COLUMN status")
    scratch.admin("ALTER TABLE {s}.users DROP COLUMN created_at")
    report = _crawl(ro_adapter, crawl_store, scratch)

    by_id = {u.id: u for u in crawl_store.list_use_cases()}
    flagged = by_id[orders_status.id]
    assert flagged.status == "needs_rereview"
    assert flagged.review_note is not None
    assert f"{scratch.schema}.orders.status" in flagged.review_note
    assert flagged.spec == orders_status.spec, "flagging must not silently change the spec"
    assert by_id[flagged_human].status == "needs_rereview"
    assert by_id[untouched].status == "confirmed"
    assert {f.id for f in report.flagged} == {orders_status.id, flagged_human}
    assert not [u for u in crawl_store.list_confirmed_use_cases() if u.id == orders_status.id]


def test_confirmed_use_case_flagged_when_table_removed(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    _crawl(ro_adapter, crawl_store, scratch)
    cart = _generated(crawl_store, "cart_with_status")
    crawl_store.set_use_case_status(cart.id, "confirmed")

    scratch.admin("DROP TABLE {s}.carts")
    _crawl(ro_adapter, crawl_store, scratch)

    after = next(u for u in crawl_store.list_use_cases() if u.id == cart.id)
    assert after.status == "needs_rereview"
    assert after.review_note is not None
    assert f"{scratch.schema}.carts" in after.review_note
    assert all(d.doc_key != f"table:{scratch.schema}.carts" for d in crawl_store.list_docs())


def test_references_outside_crawled_schemas_not_flagged(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    elsewhere = DraftCohortSpec.model_validate(
        {
            "entity": {"table": "public.users", "key": "user_id"},
            "where": {
                "type": "attribute",
                "filter": {"column": "public.users.is_active", "operator": "=", "value": True},
            },
        }
    )
    other = crawl_store.add_human_use_case("Active users", elsewhere, status="confirmed")

    _crawl(ro_adapter, crawl_store, scratch)

    assert next(u for u in crawl_store.list_use_cases() if u.id == other).status == "confirmed"


def test_failed_crawl_mid_run_leaves_previous_content_intact(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    good = _crawl(ro_adapter, crawl_store, scratch)
    crawl_store.upsert_human_doc("business-context", "note", {"text": "keep me"})
    before = _snapshot(crawl_store)

    with pytest.raises(CrawlFailedError) as excinfo:
        _crawl(FailingAdapter(ro_adapter, "orders"), crawl_store, scratch)

    assert _snapshot(crawl_store) == before
    failed = crawl_store.get_run(excinfo.value.run_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error is not None
    assert "unreachable" in failed.error
    assert failed.content_hash is None
    previous = crawl_store.get_run(good.run_id)
    assert previous is not None
    assert previous.status == "succeeded"
    latest = crawl_store.latest_run()
    assert latest is not None
    assert latest.id == excinfo.value.run_id


def test_failed_swap_rolls_back_and_marks_run_failed(
    ro_adapter: PostgresWarehouseAdapter,
    crawl_store: CrawlStore,
    scratch: ScratchWarehouse,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _crawl(ro_adapter, crawl_store, scratch)
    confirmed = _generated(crawl_store, "orders_with_status")
    crawl_store.set_use_case_status(confirmed.id, "confirmed")
    before = _snapshot(crawl_store)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk full: secret-detail-123")

    # Fails after generated docs/use cases were already deleted inside the transaction.
    monkeypatch.setattr(crawl_store, "_insert_use_cases", boom)
    scratch.admin("ALTER TABLE {s}.orders DROP COLUMN status")
    with pytest.raises(CrawlFailedError) as excinfo:
        _crawl(ro_adapter, crawl_store, scratch)

    assert _snapshot(crawl_store) == before
    failed = crawl_store.get_run(excinfo.value.run_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error is not None
    assert "secret-detail-123" not in failed.error


def test_list_confirmed_use_cases_excludes_pending(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    _crawl(ro_adapter, crawl_store, scratch)
    assert crawl_store.list_confirmed_use_cases() == []

    chosen = _generated(crawl_store, "cart_with_status")
    crawl_store.set_use_case_status(chosen.id, "confirmed")

    assert [u.id for u in crawl_store.list_confirmed_use_cases()] == [chosen.id]


@pytest.mark.parametrize("schemas", [("cs_no_such_schema",), ("SCRATCH", "cs_no_such_schema")])
def test_crawl_of_missing_schema_fails_and_keeps_previous_content(
    ro_adapter: PostgresWarehouseAdapter,
    crawl_store: CrawlStore,
    scratch: ScratchWarehouse,
    schemas: tuple[str, ...],
) -> None:
    _crawl(ro_adapter, crawl_store, scratch)
    confirmed = _generated(crawl_store, "orders_with_status")
    crawl_store.set_use_case_status(confirmed.id, "confirmed")
    before = _snapshot(crawl_store)
    configured = tuple(scratch.schema if s == "SCRATCH" else s for s in schemas)

    with pytest.raises(CrawlFailedError, match="cs_no_such_schema") as excinfo:
        run_crawl(ro_adapter, crawl_store, settings=CrawlerSettings(schemas=configured))

    assert _snapshot(crawl_store) == before
    failed = crawl_store.get_run(excinfo.value.run_id)
    assert failed is not None
    assert failed.status == "failed"


def test_crawl_where_role_can_read_no_tables_fails_and_keeps_content(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    _crawl(ro_adapter, crawl_store, scratch)
    confirmed = _generated(crawl_store, "cart_with_status")
    crawl_store.set_use_case_status(confirmed.id, "confirmed")
    before = _snapshot(crawl_store)

    scratch.admin("REVOKE SELECT ON ALL TABLES IN SCHEMA {s} FROM cohortsplit_ro")
    with pytest.raises(CrawlFailedError, match="no tables"):
        _crawl(ro_adapter, crawl_store, scratch)

    assert _snapshot(crawl_store) == before


def test_scoped_crawl_keeps_generated_docs_of_other_schemas(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    run_crawl(ro_adapter, crawl_store, settings=CrawlerSettings(schemas=("public",)))
    public_docs = [d for d in crawl_store.list_docs() if d.doc_key.startswith("table:public.")]
    assert len(public_docs) == 32

    _crawl(ro_adapter, crawl_store, scratch)

    after = crawl_store.list_docs()
    assert [d for d in after if d.doc_key.startswith("table:public.")] == public_docs
    assert any(d.doc_key.startswith(f"table:{scratch.schema}.") for d in after)


def test_failure_while_recording_failure_still_reports_crawl_error(
    ro_adapter: PostgresWarehouseAdapter,
    crawl_store: CrawlStore,
    scratch: ScratchWarehouse,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("appdb went away")

    monkeypatch.setattr(crawl_store, "mark_run_failed", broken)

    with pytest.raises(CrawlFailedError, match="unreachable"):
        _crawl(FailingAdapter(ro_adapter, "orders"), crawl_store, scratch)


def test_interrupted_crawl_is_marked_failed(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore, scratch: ScratchWarehouse
) -> None:
    class InterruptingAdapter(FailingAdapter):
        def describe_table(self, table: TableRef) -> TableMetadata:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _crawl(InterruptingAdapter(ro_adapter, "orders"), crawl_store, scratch)

    run = crawl_store.latest_run()
    assert run is not None
    assert run.status == "failed"
    assert run.error is not None
    assert "interrupted" in run.error
