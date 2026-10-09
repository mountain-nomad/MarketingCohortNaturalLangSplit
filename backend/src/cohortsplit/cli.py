"""``cohortsplit`` command-line entry point."""

import argparse
from collections.abc import Callable, Sequence

from cohortsplit import __version__
from cohortsplit.auth.cli import register_subcommands as register_auth_subcommands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cohortsplit",
        description="CohortSplit administration commands.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(title="commands", metavar="<command>")
    register_auth_subcommands(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)
