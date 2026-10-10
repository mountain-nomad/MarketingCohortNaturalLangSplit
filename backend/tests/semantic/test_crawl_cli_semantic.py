"""``cohortsplit crawl`` shares the API's crawl lock and records the semantic version."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from cohortsplit.crawler import cli as crawl_cli
from cohortsplit.semantic.crawl import CRAWL_RUN_LOCK_KEY
from tests.constants import APPDB_PASSWORD
from tests.integration.conftest import ScratchWarehouse


@pytest.fixture
def cli_env(
    engine: Engine,
    clean_env: pytest.MonkeyPatch,
    scratch: ScratchWarehouse,
    warehouse_ro_dsn: str,
) -> Iterator[pytest.MonkeyPatch]:
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", APPDB_PASSWORD)
    clean_env.setenv("COHORTSPLIT_WAREHOUSE_DSN", warehouse_ro_dsn)
    clean_env.setenv("COHORTSPLIT_CRAWLER_SCHEMAS", scratch.schema)
    clean_env.setattr(crawl_cli, "create_appdb_engine", lambda _settings: engine)
    clean_env.setattr(engine, "dispose", lambda: None)  # keep the shared test database alive
    yield clean_env


def _versions(engine: Engine) -> list[tuple[str, str, str | None]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT cause, actor_type, target FROM semantic_versions ORDER BY id")
        ).all()
    return [(r.cause, r.actor_type, r.target) for r in rows]


def test_cli_crawl_records_version(
    engine: Engine, cli_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = crawl_cli.run(crawl_cli.argparse.Namespace(json=False))

    assert code == 0, capsys.readouterr().err
    [(cause, actor_type, target)] = _versions(engine)
    assert (cause, actor_type) == ("crawler.run", "cli")
    assert target is not None
    assert target.startswith("crawl_run:")


def test_cli_refuses_while_another_crawl_runs(
    engine: Engine, cli_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": CRAWL_RUN_LOCK_KEY})
        conn.commit()
        try:
            code = crawl_cli.run(crawl_cli.argparse.Namespace(json=False))
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": CRAWL_RUN_LOCK_KEY})
            conn.commit()

    assert code == 1
    assert "another crawl is running" in capsys.readouterr().err.lower()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM crawl_runs")).scalar() == 0
