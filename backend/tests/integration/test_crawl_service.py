"""End-to-end crawl of the demo warehouse: service, CLI and credential hygiene (AC-22, AC-28)."""

import json
import logging

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr
from sqlalchemy import Engine, text

from cohortsplit.cli import main
from cohortsplit.crawler.service import CrawlFailedError, run_crawl
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.crawler.store import CrawlStore
from cohortsplit.warehouse import PostgresWarehouseAdapter, ReadOnlyExecutor

pytestmark = pytest.mark.integration


def _password(dsn: str) -> str:
    password = conninfo_to_dict(dsn).get("password")
    assert password, "integration DSN must carry a password"
    return str(password)


def _all_crawler_rows(engine: Engine) -> str:
    with engine.connect() as conn:
        rows = [
            conn.execute(text(f"SELECT to_jsonb(t)::text FROM {table} t")).scalars().all()  # noqa: S608
            for table in ("crawl_runs", "semantic_docs", "example_use_cases")
        ]
    return "\n".join(r for chunk in rows for r in chunk)


def test_demo_crawl_end_to_end(
    ro_adapter: PostgresWarehouseAdapter, crawl_store: CrawlStore
) -> None:
    report = run_crawl(ro_adapter, crawl_store, settings=CrawlerSettings(schemas=("public",)))

    assert report.table_count == 16
    assert report.location.endswith("/ecommerce")
    schema_docs = crawl_store.list_docs(origin="generated", kind="table_schema")
    profiles = crawl_store.list_docs(origin="generated", kind="data_profile")
    assert len(schema_docs) == 16
    assert len(profiles) == 16
    orders = next(p for p in profiles if p.doc_key == "table:public.orders")
    status = next(c for c in orders.content["columns"] if c["name"] == "status")
    assert "delivered" in status["values"]
    users = next(p for p in profiles if p.doc_key == "table:public.users")
    for column in users.content["columns"]:
        if column["name"] in ("email", "phone", "password_hash"):
            assert column["values"] is None
    use_cases = crawl_store.list_use_cases()
    assert len(use_cases) >= 8
    assert {u.status for u in use_cases} == {"pending_review"}
    liked = next(u for u in use_cases if u.template_key == "liked_product")
    assert liked.rewritten_from is not None
    assert liked.generation_note is not None


def test_crawl_logs_and_stored_content_contain_no_credentials(
    ro_adapter: PostgresWarehouseAdapter,
    crawl_store: CrawlStore,
    migrated_appdb: Engine,
    warehouse_ro_dsn: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    run_crawl(ro_adapter, crawl_store, settings=CrawlerSettings(schemas=("public",)))

    stored = _all_crawler_rows(migrated_appdb)
    password = _password(warehouse_ro_dsn)
    for text_blob in (caplog.text, stored):
        assert password not in text_blob
        assert warehouse_ro_dsn not in text_blob
        assert "postgresql://" not in text_blob


def test_crawl_failure_with_wrong_password_is_sanitized(
    crawl_store: CrawlStore,
    migrated_appdb: Engine,
    warehouse_ro_dsn: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    wrong = "wrong-pw-5d0e77"
    params = conninfo_to_dict(warehouse_ro_dsn)
    params["password"] = wrong
    adapter = PostgresWarehouseAdapter(ReadOnlyExecutor(SecretStr(make_conninfo(**params))))  # type: ignore[arg-type]

    with pytest.raises(CrawlFailedError) as excinfo:
        run_crawl(adapter, crawl_store, settings=CrawlerSettings(schemas=("public",)))

    run = crawl_store.get_run(excinfo.value.run_id)
    assert run is not None
    assert run.status == "failed"
    assert run.error is not None
    assert "unreachable" in run.error
    for text_blob in (
        str(excinfo.value),
        run.error,
        caplog.text,
        _all_crawler_rows(migrated_appdb),
    ):
        assert wrong not in text_blob


def test_crawl_cli_against_demo(
    crawl_store: CrawlStore,
    warehouse_ro_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COHORTSPLIT_CRAWLER_SCHEMAS", "public")

    assert main(["crawl"]) == 0
    human = capsys.readouterr()
    assert main(["crawl", "--json"]) == 0
    machine = capsys.readouterr()

    assert "16 tables" in human.out
    assert "pending review" in human.out
    summary = json.loads(machine.out)
    assert summary["tables"] == 16
    assert summary["status"] == "succeeded"
    assert len(summary["content_hash"]) == 64
    password = _password(warehouse_ro_dsn)
    for blob in (human.out, human.err, machine.out, machine.err):
        assert password not in blob
    run = crawl_store.latest_run()
    assert run is not None
    assert run.triggered_by == "cli"


def test_crawl_cli_unreachable_warehouse_exit_code(
    crawl_store: CrawlStore,
    closed_port: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "cli-secret-pw-4a1b"
    monkeypatch.setenv(
        "COHORTSPLIT_WAREHOUSE_DSN",
        f"postgresql://cohortsplit_ro:{secret}@127.0.0.1:{closed_port}/ecommerce",
    )

    code = main(["crawl"])

    out = capsys.readouterr()
    assert code == 1
    assert f"127.0.0.1:{closed_port}/ecommerce" in out.err
    assert "previous" in out.err.lower()
    assert secret not in out.out + out.err


def test_crawler_role_is_read_only(warehouse_ro_dsn: str) -> None:
    """The crawler connects as the read-only role; it cannot write even if it tried (AC-12)."""
    with psycopg.connect(warehouse_ro_dsn) as conn:
        row = conn.execute("SELECT current_user").fetchone()
    assert row == ("cohortsplit_ro",)
