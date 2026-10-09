"""``cohortsplit`` command-line entry point.

Subcommands are registered by their feature packages (e.g. ``cohortsplit crawl``
from :mod:`cohortsplit.crawler.cli`).
"""

import argparse
from collections.abc import Sequence

from cohortsplit import __version__
from cohortsplit.crawler.cli import register as register_crawl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cohortsplit",
        description="CohortSplit administration commands.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    register_crawl(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))
