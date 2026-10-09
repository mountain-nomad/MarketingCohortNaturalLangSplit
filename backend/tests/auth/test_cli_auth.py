"""``cohortsplit create-admin`` / ``reset-password`` (FR-A7, AC-A1)."""

from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit import cli as root_cli
from cohortsplit.auth import cli as auth_cli
from cohortsplit.auth.catalog import ALL_PERMISSIONS
from cohortsplit.auth.models import User
from tests.auth.conftest import AuditReader
from tests.auth.helpers import (
    OTHER_PASSWORD,
    STRONG_PASSWORD,
    ApiClient,
    FakeClock,
    admin_role,
    error_code,
    make_user,
)


class FakePrompt:
    """Stands in for getpass.getpass and records the prompts it was shown."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "Password: ") -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)


@pytest.fixture
def cli_env(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., int]]:
    """Run ``cohortsplit <args>`` against the test database with a fake getpass."""
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(auth_cli, "session_factory_from_settings", lambda: factory)

    def run(*args: str, answers: tuple[str, ...] = ()) -> int:
        prompt = FakePrompt(*answers)
        monkeypatch.setattr(auth_cli.getpass, "getpass", prompt)
        run.prompt = prompt  # type: ignore[attr-defined]
        return root_cli.main(list(args))

    yield run


def test_create_admin_user_can_log_in_and_has_every_permission(
    cli_env: Callable[..., int], app: FastAPI
) -> None:
    code = cli_env(
        "create-admin", "--email", "Root@Example.com", answers=(STRONG_PASSWORD, STRONG_PASSWORD)
    )

    assert code == 0
    client = ApiClient(app)
    login = client.login("root@example.com")
    assert login.status_code == 200
    assert login.json()["must_change_password"] is False
    me = client.get("/api/me").json()
    assert me["is_admin"] is True
    assert set(me["permissions"]) == ALL_PERMISSIONS


def test_create_admin_prompts_twice_with_getpass(cli_env: Callable[..., int]) -> None:
    cli_env("create-admin", "--email", "root@example.com", answers=(STRONG_PASSWORD,) * 2)

    prompts = cli_env.prompt.prompts  # type: ignore[attr-defined]
    assert len(prompts) == 2


def test_create_admin_does_not_accept_password_argument(
    cli_env: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_env("create-admin", "--email", "root@example.com", "--password", STRONG_PASSWORD)

    assert excinfo.value.code == 2


def test_create_admin_refuses_mismatched_confirmation(
    cli_env: Callable[..., int], db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_env(
        "create-admin", "--email", "root@example.com", answers=(STRONG_PASSWORD, OTHER_PASSWORD)
    )

    assert code != 0
    assert db.execute(select(User)).first() is None
    assert STRONG_PASSWORD not in capsys.readouterr().err


def test_create_admin_enforces_password_policy(
    cli_env: Callable[..., int], db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_env("create-admin", "--email", "root@example.com", answers=("short",) * 2)

    assert code != 0
    assert db.execute(select(User)).first() is None
    assert "12" in capsys.readouterr().err


def test_create_admin_rejects_invalid_email(cli_env: Callable[..., int], db: Session) -> None:
    code = cli_env("create-admin", "--email", "not-an-email", answers=(STRONG_PASSWORD,) * 2)

    assert code != 0
    assert db.execute(select(User)).first() is None


def test_create_admin_is_audited_with_cli_actor(
    cli_env: Callable[..., int], audit_events: AuditReader
) -> None:
    cli_env("create-admin", "--email", "root@example.com", answers=(STRONG_PASSWORD,) * 2)

    [created] = audit_events.of("user.create")
    assert created.actor_type == "cli"
    assert created.actor_user_id is None
    assert created.outcome == "success"
    [assigned] = audit_events.of("role.assign")
    assert assigned.actor_type == "cli"
    assert assigned.metadata_["role_name"] == "Admin"


def test_create_admin_on_existing_email_grants_admin_and_reactivates(
    cli_env: Callable[..., int],
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    audit_events: AuditReader,
) -> None:
    user = make_user(db, "bob@example.com", now=clock.now, is_active=False)

    code = cli_env("create-admin", "--email", "BOB@example.com")

    assert code == 0
    db.expire_all()
    stored = db.get(User, user.id)
    assert stored is not None
    assert stored.is_active is True
    assert [r.name for r in stored.roles] == ["Admin"]
    # Password unchanged on the recovery path (ruling 4); no prompt needed.
    assert cli_env.prompt.prompts == []  # type: ignore[attr-defined]
    client = ApiClient(app)
    assert client.login("bob@example.com", STRONG_PASSWORD).status_code == 200
    assert client.get("/api/me").json()["is_admin"] is True
    assert [e.actor_type for e in audit_events.of("user.reactivate")] == ["cli"]
    assert [e.actor_type for e in audit_events.of("role.assign")] == ["cli"]


def test_reset_password_sets_temporary_password_and_revokes_sessions(
    cli_env: Callable[..., int],
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    audit_events: AuditReader,
) -> None:
    make_user(db, "bob@example.com", now=clock.now)
    old_session = ApiClient(app)
    old_session.login("bob@example.com")

    code = cli_env("reset-password", "--email", "bob@example.com", answers=(OTHER_PASSWORD,) * 2)

    assert code == 0
    assert old_session.get("/api/me").status_code == 401
    client = ApiClient(app)
    login = client.login("bob@example.com", OTHER_PASSWORD)
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    assert error_code(client.get("/api/me")) == "password_change_required"
    [event] = audit_events.of("user.password_reset")
    assert event.actor_type == "cli"


def test_reset_password_clears_lockout(
    cli_env: Callable[..., int], app: FastAPI, db: Session, clock: FakeClock
) -> None:
    make_user(db, "root@example.com", now=clock.now, roles=[admin_role(db)])
    for _ in range(5):
        ApiClient(app).login("root@example.com", OTHER_PASSWORD)
    assert ApiClient(app).login("root@example.com").status_code == 429

    cli_env("reset-password", "--email", "root@example.com", answers=(OTHER_PASSWORD,) * 2)

    assert ApiClient(app).login("root@example.com", OTHER_PASSWORD).status_code == 200


def test_reset_password_for_unknown_email_fails(
    cli_env: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_env("reset-password", "--email", "ghost@example.com")

    assert code != 0
    assert "ghost@example.com" in capsys.readouterr().err
