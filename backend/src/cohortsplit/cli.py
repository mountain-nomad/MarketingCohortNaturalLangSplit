"""``cohortsplit`` command-line entry point.

Administrative commands (e.g. first-admin bootstrap) arrive with later features.
"""

import argparse
from collections.abc import Sequence

from cohortsplit import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cohortsplit",
        description="CohortSplit administration commands.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
