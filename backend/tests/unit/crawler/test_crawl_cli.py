"""``cohortsplit crawl`` fails with actionable messages and no credentials (AC-28)."""

import logging

import pytest

from cohortsplit.cli import main
from tests.constants import APPDB_PASSWORD, WAREHOUSE_PASSWORD


def test_crawl_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["crawl", "--help"])

    assert excinfo.value.code == 0
    assert "usage: cohortsplit crawl" in capsys.readouterr().out


def test_top_level_help_lists_crawl(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])

    assert "crawl" in capsys.readouterr().out


def test_crawl_without_warehouse_dsn_exits_2(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", APPDB_PASSWORD)

    code = main(["crawl"])

    err = capsys.readouterr().err
    assert code == 2
    assert "COHORTSPLIT_WAREHOUSE_DSN" in err


def test_crawl_invalid_crawler_setting_exits_2(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clean_env.setenv("COHORTSPLIT_APPDB_PASSWORD", APPDB_PASSWORD)
    clean_env.setenv("COHORTSPLIT_CRAWLER_SAMPLE_MAX_DISTINCT", "0")

    code = main(["crawl"])

    assert code == 2
    assert "COHORTSPLIT_CRAWLER_SAMPLE_MAX_DISTINCT" in capsys.readouterr().err


def test_crawl_cli_unreachable_output_has_no_password(
    unreachable_appdb_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    code = main(["crawl"])

    out = capsys.readouterr()
    assert code == 1
    assert "unreachable" in out.err.lower()
    for blob in (out.out, out.err, caplog.text):
        assert WAREHOUSE_PASSWORD not in blob
        assert APPDB_PASSWORD not in blob
