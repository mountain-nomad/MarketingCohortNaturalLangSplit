"""Operator CLI commands: ``create-admin`` and ``reset-password`` (FR-A7).

Passwords are read only through ``getpass`` (no echo, never a command-line flag or an
environment variable, so they stay out of shell history and process listings). Both
commands are audited with actor ``cli``; shell access is fully trusted.
"""

import argparse
import getpass
import sys

from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.audit import actions
from cohortsplit.audit.service import Actor, AuditEventIn, AuditService
from cohortsplit.auth.clock import utcnow
from cohortsplit.auth.passwords import PasswordPolicyError, validate_password_policy
from cohortsplit.auth.user_admin import MAX_DISPLAY_NAME_LENGTH
from cohortsplit.auth.users import (
    InvalidEmailError,
    admin_role,
    assign_role_audited,
    create_user,
    find_by_email,
    set_temporary_password,
    validate_email,
)
from cohortsplit.config import ConfigError, load_settings
from cohortsplit.db import create_appdb_engine

__all__ = ["getpass", "register_subcommands", "session_factory_from_settings"]


def session_factory_from_settings() -> sessionmaker[Session]:
    return sessionmaker(create_appdb_engine(load_settings()), expire_on_commit=False)


def _fail(message: str, code: int = 1) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


def _prompt_password(label: str) -> str | None:
    first = getpass.getpass(f"{label}: ")
    second = getpass.getpass(f"Repeat {label.lower()}: ")
    if first != second:
        _fail("passwords do not match; nothing was changed.")
        return None
    try:
        validate_password_policy(first)
    except PasswordPolicyError as exc:
        _fail(f"{exc} Nothing was changed.")
        return None
    return first


def _open() -> tuple[sessionmaker[Session], AuditService] | int:
    try:
        factory = session_factory_from_settings()
    except ConfigError as exc:
        return _fail(str(exc), 2)
    return factory, AuditService(factory)


def _create_admin(args: argparse.Namespace) -> int:
    try:
        email = validate_email(args.email)
    except InvalidEmailError as exc:
        return _fail(str(exc))
    opened = _open()
    if isinstance(opened, int):
        return opened
    factory, audit = opened
    now = utcnow()
    actor = Actor.cli()

    with factory() as db:
        user = find_by_email(db, email)
        password = None
        if user is None:
            # Prompt before taking any lock: the operator may take their time.
            password = _prompt_password("Password")
            if password is None:
                return 1
        role = admin_role(db, for_update=True)
        user = find_by_email(db, email)
        if user is None:
            if password is None:  # created concurrently and removed again: never happens
                return _fail("user state changed while prompting; run the command again")
            user = create_user(
                db,
                email=email,
                display_name=email.split("@")[0][:MAX_DISPLAY_NAME_LENGTH],
                password=password,
                must_change_password=False,
                now=now,
            )
            audit.record(
                db,
                AuditEventIn(
                    action=actions.USER_CREATE,
                    outcome="success",
                    actor=actor,
                    occurred_at=now,
                    target_type="user",
                    target_id=str(user.id),
                    metadata={"via": "create-admin"},
                ),
            )
            message = f"Created admin user {email}."
        else:
            if not user.is_active:
                user.is_active = True
                user.updated_at = now
                audit.record(
                    db,
                    AuditEventIn(
                        action=actions.USER_REACTIVATE,
                        outcome="success",
                        actor=actor,
                        occurred_at=now,
                        target_type="user",
                        target_id=str(user.id),
                        metadata={"via": "create-admin"},
                    ),
                )
            message = (
                f"Granted the Admin role to existing user {email} (active). "
                "The password was not changed; use `cohortsplit reset-password` if needed."
            )
        assign_role_audited(db, audit, user=user, role=role, actor=actor, now=now, request_id=None)
        db.commit()
    print(message)
    return 0


def _reset_password(args: argparse.Namespace) -> int:
    opened = _open()
    if isinstance(opened, int):
        return opened
    factory, audit = opened
    now = utcnow()

    with factory() as db:
        user = find_by_email(db, args.email)
        if user is None:
            return _fail(f"no user with email {args.email.strip().lower()}")
        password = _prompt_password("Temporary password")
        if password is None:
            return 1
        set_temporary_password(db, user, password, now)
        audit.record(
            db,
            AuditEventIn(
                action=actions.USER_PASSWORD_RESET,
                outcome="success",
                actor=Actor.cli(),
                occurred_at=now,
                target_type="user",
                target_id=str(user.id),
                metadata={"via": "reset-password"},
            ),
        )
        db.commit()
        email = user.email
    print(
        f"Temporary password set for {email}. They must change it at next sign-in; "
        "their sessions were signed out."
    )
    return 0


def register_subcommands(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    create = subparsers.add_parser(
        "create-admin",
        help="create an admin user (password prompted), or grant Admin to an existing user",
    )
    create.add_argument("--email", required=True)
    create.set_defaults(handler=_create_admin)

    reset = subparsers.add_parser(
        "reset-password", help="set a temporary password for a user (prompted)"
    )
    reset.add_argument("--email", required=True)
    reset.set_defaults(handler=_reset_password)
