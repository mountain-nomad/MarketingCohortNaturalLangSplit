"""``cohortsplit crawl`` subcommand."""

import argparse
from typing import Any


def register(subparsers: "argparse._SubParsersAction[Any]") -> None:
    parser = subparsers.add_parser(
        "crawl",
        help="crawl the configured warehouse and store generated docs and example use cases",
        description=(
            "Crawl the configured warehouse (read-only) and replace generated schema docs, "
            "data profiles and pending example use cases. Human-authored content and "
            "reviewed use cases are preserved."
        ),
    )
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    raise NotImplementedError
