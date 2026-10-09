"""The ``cohortsplit`` console script is installed and answers --help."""

import subprocess
import sys
from pathlib import Path

import pytest

from cohortsplit.cli import main


def test_console_script_help_exits_zero() -> None:
    script = Path(sys.executable).parent / "cohortsplit"
    assert script.exists(), f"console script not installed at {script}"

    result = subprocess.run(
        [str(script), "--help"], capture_output=True, text=True, timeout=30, check=False
    )

    assert result.returncode == 0, result.stderr
    assert "usage: cohortsplit" in result.stdout


def test_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    assert "usage: cohortsplit" in capsys.readouterr().out
