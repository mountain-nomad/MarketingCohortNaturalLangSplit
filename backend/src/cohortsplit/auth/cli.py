"""Operator CLI commands: ``create-admin`` and ``reset-password`` (FR-A7)."""

import argparse
import getpass

from sqlalchemy.orm import Session, sessionmaker

__all__ = ["getpass", "register_subcommands", "session_factory_from_settings"]


def session_factory_from_settings() -> sessionmaker[Session]:
    raise NotImplementedError


def _create_admin(args: argparse.Namespace) -> int:
    raise NotImplementedError


def _reset_password(args: argparse.Namespace) -> int:
    raise NotImplementedError


def register_subcommands(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    create = subparsers.add_parser("create-admin", help="create or recover an admin user")
    create.add_argument("--email", required=True)
    create.set_defaults(handler=_create_admin)

    reset = subparsers.add_parser("reset-password", help="set a temporary password for a user")
    reset.add_argument("--email", required=True)
    reset.set_defaults(handler=_reset_password)
