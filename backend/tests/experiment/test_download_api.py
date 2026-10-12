"""POST /api/runs/{id}/download: export ZIP from the stored snapshot, column grants checked
at every download (FR-9, FR-9a, FR-A4, AC-19 .. AC-21b, AC-A15 .. AC-A17).
Runs on SQLite and PostgreSQL."""

import dataclasses
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cohortsplit.auth.models import RoleExportColumn, RolePermission, User, UserRole
from cohortsplit.experiment.export import CSV_ESCAPING_RULE
from cohortsplit.experiment.models import ExperimentRun
from cohortsplit.warehouse.errors import QueryTimeoutError, WarehouseUnavailableError
from tests.auth.helpers import ApiClient, FakeClock, error_code
from tests.cohort.helpers import install_environment
from tests.experiment.helpers import (
    FIRST_NAME,
    KEY,
    PHONE,
    ExportExecutor,
    download,
    exporter,
    read_export,
    snapshot_of,
    split_ok,
)

MEMBERS = list(range(1, 31))
PHONES = {m: f"+49151{m:05d}" for m in MEMBERS} | {5: None, 6: '=HYPERLINK("http://x")'}


def phone_executor(**extra: Any) -> ExportExecutor:
    return ExportExecutor(members=list(MEMBERS), columns={PHONE: dict(PHONES)}, **extra)


def revoke(db: Session, column: str) -> None:
    db.execute(delete(RoleExportColumn).where(RoleExportColumn.column_ref == column))
    db.commit()


def grant(db: Session, email: str, column: str) -> None:
    user = db.scalar(select(User).where(User.email == email))
    assert user is not None
    role_id = db.scalar(select(UserRole.role_id).where(UserRole.user_id == user.id))
    assert role_id is not None
    db.add(RoleExportColumn(role_id=role_id, column_ref=column))
    db.commit()


def download_count(db: Session, run_id: str) -> int:
    db.expire_all()
    run = db.get(ExperimentRun, run_id)
    assert run is not None
    return run.download_count


def test_ac21_zip_layout_and_manifest(app: FastAPI, db: Session, clock: FakeClock) -> None:
    client = exporter(app, db, clock)
    install_environment(app, executor=ExportExecutor(members=list(MEMBERS)))
    run = split_ok(client, allocation={"test": 70, "control": 30})

    response = download(client, run["id"])
    export = read_export(response)

    assert f'filename="cohortsplit_{KEY}.zip"' in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert export.names == [f"{KEY}_test.csv", f"{KEY}_control.csv", "manifest.json"]
    assert export.test[0] == ["user_id"]
    assert export.control[0] == ["user_id"]
    snapshot = snapshot_of(db, run["id"])
    assert export.ids("test") == [u for u in map(str, MEMBERS) if snapshot[u] == "test"]
    assert export.ids("control") == [u for u in map(str, MEMBERS) if snapshot[u] == "control"]
    manifest = export.manifest
    detail = client.get(f"/api/runs/{run['id']}").json()
    assert manifest["run_id"] == run["id"]
    assert manifest["nl_request"] == detail["nl_request"]
    assert manifest["spec"] == detail["spec"]
    assert manifest["sql"] == detail["sql"]
    assert manifest["semantic_version"] == detail["semantic_version"]
    assert manifest["experiment"]["key"] == KEY
    assert manifest["experiment"]["allocation"] == {"test": 70, "control": 30}
    assert manifest["counts"] == {
        "test": len(export.ids("test")),
        "control": len(export.ids("control")),
        "total": 30,
    }
    assert manifest["created_at"] == run["created_at"]
    assert manifest["export"]["exported_at"] == clock.now.isoformat()
    assert manifest["export"]["redownload"] is False
    assert manifest["export"]["columns"] == []
    assert manifest["export"]["missing_from_warehouse"] is None
    assert manifest["export"]["csv_formula_escaping"] == CSV_ESCAPING_RULE


def test_ac20_granted_phone_exported_one_row_per_user(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    install_environment(app, executor=phone_executor())
    run = split_ok(client, export_columns=[PHONE])

    export = read_export(download(client, run["id"]))

    assert export.test[0] == ["user_id", "phone"]
    rows = export.test[1:] + export.control[1:]
    assert len(rows) == 30
    assert len({row[0] for row in rows}) == 30  # one row per user
    by_id = {row[0]: row[1] for row in rows}
    assert by_id["1"] == "'+4915100001"  # formula guard on a leading '+'
    assert by_id["5"] == ""  # NULL phone stays in the cohort
    assert by_id["6"] == '\'=HYPERLINK("http://x")'
    assert export.manifest["export"]["columns"] == [PHONE]
    assert export.manifest["export"]["missing_from_warehouse"] == {"test": 0, "control": 0}


def test_column_fetch_is_parameterized_validated_and_batched(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    from cohortsplit.experiment.service import EXPORT_BATCH_SIZE

    many = list(range(1, 2 * EXPORT_BATCH_SIZE + 6))
    client = exporter(app, db, clock, columns=[PHONE])
    env = install_environment(
        app, executor=ExportExecutor(members=many, columns={PHONE: {m: "1" for m in many}})
    )
    run = split_ok(client, export_columns=[PHONE])

    export = read_export(download(client, run["id"]))

    assert len(export.test) + len(export.control) - 2 == len(many)
    statements = env.executor.column_statements
    assert len(statements) >= 3
    for statement in statements:
        assert statement.params is not None
        assert 0 < len(statement.params) <= EXPORT_BATCH_SIZE
        assert statement.row_cap == len(statement.params)
        # Ids are bound parameters, never SQL text.
        assert set(statement.params.values()) <= set(many)
        placeholders = ", ".join(f"%(p{i})s" for i in range(1, len(statement.params) + 1))
        assert statement.sql.endswith(f'WHERE u."user_id" IN ({placeholders})')


def test_explicit_columns_override_and_empty_list_means_id_only(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock, columns=[PHONE, FIRST_NAME])
    install_environment(
        app,
        executor=ExportExecutor(
            members=list(MEMBERS),
            columns={PHONE: dict(PHONES), FIRST_NAME: dict.fromkeys(MEMBERS, "Ann")},
        ),
    )
    run = split_ok(client, export_columns=[PHONE])

    names = read_export(download(client, run["id"], columns=[FIRST_NAME, PHONE]))
    only_id = read_export(download(client, run["id"], columns=[]))

    assert names.test[0] == ["user_id", "first_name", "phone"]
    assert only_id.test[0] == ["user_id"]


def test_ac19_phone_without_grant_refused(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: Any
) -> None:
    client = exporter(app, db, clock)
    env = install_environment(app, executor=phone_executor())
    run = split_ok(client)

    refused = download(client, run["id"], columns=[PHONE])

    assert refused.status_code == 403
    assert error_code(refused) == "export_column_denied"
    assert refused.json()["error"]["columns"] == [PHONE]
    assert env.executor.column_statements == []
    [denied] = audit_events.of("cohort.export")
    assert denied.outcome == "denied"
    assert denied.target_id == run["id"]
    assert denied.metadata_["denied_columns"] == [PHONE]
    assert download_count(db, run["id"]) == 0

    # The id-only export works; a later re-download with phone is refused too.
    read_export(download(client, run["id"]))
    again = download(client, run["id"], columns=[PHONE])
    assert again.status_code == 403
    assert error_code(again) == "export_column_denied"
    [redownload_denied] = [e for e in audit_events.of("cohort.redownload") if e.outcome == "denied"]
    assert redownload_denied.metadata_["denied_columns"] == [PHONE]


def test_ac21a_redownload_membership_identical_even_if_warehouse_changes(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    env = install_environment(app, executor=phone_executor())
    run = split_ok(client, export_columns=[PHONE])
    original = read_export(download(client, run["id"]))

    def membership_queries() -> int:
        return sum(1 for s in env.executor.statements if s.sql.startswith("SELECT DISTINCT u."))

    before = membership_queries()

    env.executor.members = [1, 2, 999]  # the cohort definition now matches other users
    clock.advance(days=10)
    assert client.login("marketer@example.com").status_code == 200  # the session idled out
    again = read_export(download(client, run["id"]))

    assert again.ids("test") == original.ids("test")
    assert again.ids("control") == original.ids("control")
    assert again.manifest["counts"] == original.manifest["counts"]
    assert again.manifest["export"]["redownload"] is True
    # Runs are never re-executed: no cohort query ran for the re-download.
    assert membership_queries() == before


def test_users_missing_from_the_warehouse_keep_their_row_and_are_counted(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    env = install_environment(app, executor=phone_executor())
    run = split_ok(client, export_columns=[PHONE])
    snapshot = snapshot_of(db, run["id"])
    gone = {1, 2, 3}
    env.executor.warehouse_users = set(MEMBERS) - gone

    export = read_export(download(client, run["id"]))

    rows = {row[0]: row[1] for row in export.test[1:] + export.control[1:]}
    assert len(rows) == 30
    assert all(rows[str(u)] == "" for u in gone)
    missing = export.manifest["export"]["missing_from_warehouse"]
    assert missing == {
        "test": sum(1 for u in gone if snapshot[str(u)] == "test"),
        "control": sum(1 for u in gone if snapshot[str(u)] == "control"),
    }


def test_ac21b_redownload_after_grant_revoked_refused(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: Any
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    install_environment(app, executor=phone_executor())
    run = split_ok(client, export_columns=[PHONE])
    read_export(download(client, run["id"]))

    revoke(db, PHONE)
    refused = download(client, run["id"])  # the run's columns: phone

    assert refused.status_code == 403
    assert error_code(refused) == "export_column_denied"
    assert any(
        e.outcome == "denied" and e.metadata_["denied_columns"] == [PHONE]
        for e in audit_events.of("cohort.redownload")
    )
    # The canonical id alone is still exportable.
    assert read_export(download(client, run["id"], columns=[])).test[0] == ["user_id"]


def test_a16_grant_added_then_revoked(app: FastAPI, db: Session, clock: FakeClock) -> None:
    client = exporter(app, db, clock)
    install_environment(app, executor=phone_executor())
    run = split_ok(client)

    assert download(client, run["id"], columns=[PHONE]).status_code == 403
    grant(db, "marketer@example.com", PHONE)
    assert read_export(download(client, run["id"], columns=[PHONE])).test[0] == [
        "user_id",
        "phone",
    ]
    revoke(db, PHONE)
    assert download(client, run["id"], columns=[PHONE]).status_code == 403


def test_a17_canonical_id_with_export_permission_only(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock)
    install_environment(app, executor=phone_executor())
    run = split_ok(client)
    db.execute(delete(RolePermission).where(RolePermission.permission_key == "cohort.create"))
    db.commit()

    export = read_export(download(client, run["id"]))

    assert export.test[0] == ["user_id"]
    assert len(export.ids("test")) + len(export.ids("control")) == 30


def test_admin_has_no_implicit_column_grants(
    app: FastAPI, db: Session, clock: FakeClock, admin_client: ApiClient
) -> None:
    client = exporter(app, db, clock)
    install_environment(app, executor=phone_executor())
    run = split_ok(client)

    response = download(admin_client, run["id"], columns=[PHONE])

    assert response.status_code == 403
    assert error_code(response) == "export_column_denied"


def test_zero_group_needs_explicit_confirmation(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock)
    env = install_environment(app, executor=ExportExecutor(members=[6]))
    run = split_ok(client)

    refused = download(client, run["id"])
    assert refused.status_code == 409
    assert error_code(refused) == "empty_group_confirmation_required"
    assert refused.json()["error"]["groups"] == ["control"]
    assert env.executor.column_statements == []

    export = read_export(download(client, run["id"], confirm_empty_group=True))
    assert export.ids("test") == ["6"]
    assert export.ids("control") == []
    assert export.control == [["user_id"]]


def test_column_not_in_the_user_table_is_unsupported(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: Any
) -> None:
    client = exporter(app, db, clock, columns=["public.orders.status"])
    install_environment(app, executor=phone_executor())
    run = split_ok(client)

    response = download(client, run["id"], columns=["public.orders.status"])

    assert response.status_code == 422
    assert error_code(response) == "export_column_unsupported"
    assert not [e for e in audit_events.of("cohort.export") if e.outcome == "success"]


def test_canonical_user_id_changed_refuses_extra_columns_only(
    app: FastAPI, db: Session, clock: FakeClock
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    env = install_environment(app, executor=phone_executor())
    run = split_ok(client, export_columns=[PHONE])
    env.snapshots[0] = dataclasses.replace(
        env.snapshot(), canonical_user_id="public.addresses.address_id"
    )

    refused = download(client, run["id"])

    assert refused.status_code == 409
    assert error_code(refused) == "canonical_user_id_changed"
    assert env.executor.column_statements == []
    assert read_export(download(client, run["id"], columns=[])).ids("test")


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (QueryTimeoutError(30), 504, "query_timeout"),
        (WarehouseUnavailableError("warehouse down"), 503, "warehouse_unavailable"),
    ],
)
def test_warehouse_failure_at_download_gives_no_partial_export(
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    audit_events: Any,
    error: Exception,
    status: int,
    code: str,
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    env = install_environment(app, executor=phone_executor())
    run = split_ok(client, export_columns=[PHONE])
    env.executor.column_error = error

    response = download(client, run["id"])

    assert response.status_code == status
    assert error_code(response) == code
    assert response.headers["content-type"].startswith("application/json")
    assert download_count(db, run["id"]) == 0
    assert any(e.outcome == "error" for e in audit_events.of("cohort.export"))


def test_download_count_and_redownload_flag(app: FastAPI, db: Session, clock: FakeClock) -> None:
    client = exporter(app, db, clock)
    install_environment(app, executor=phone_executor())
    run = split_ok(client)

    first = read_export(download(client, run["id"]))
    second = read_export(download(client, run["id"]))

    assert first.manifest["export"]["redownload"] is False
    assert second.manifest["export"]["redownload"] is True
    assert download_count(db, run["id"]) == 2
    assert client.get(f"/api/runs/{run['id']}").json()["download_count"] == 2


def test_aa_manifest(app: FastAPI, db: Session, clock: FakeClock) -> None:
    client = exporter(app, db, clock)
    install_environment(app, executor=phone_executor())
    run = split_ok(client, experiment_type="aa")

    manifest = read_export(download(client, run["id"])).manifest

    assert manifest["experiment"]["type"] == "A/A"


@pytest.mark.parametrize(
    "body", [{"columns": ["phone"]}, {"confirm_empty_group": "maybe"}, {"x": 1}]
)
def test_download_body_is_validated(
    app: FastAPI, db: Session, clock: FakeClock, body: dict[str, Any]
) -> None:
    client = exporter(app, db, clock)
    install_environment(app, executor=phone_executor())
    run = split_ok(client)

    response = client.post(f"/api/runs/{run['id']}/download", json=body)

    assert response.status_code == 422
    assert error_code(response) == "validation_error"


def test_unknown_run_is_not_found(app: FastAPI, db: Session, clock: FakeClock) -> None:
    client = exporter(app, db, clock)
    install_environment(app, executor=phone_executor())

    response = download(client, "0" * 32)

    assert response.status_code == 404
    assert error_code(response) == "run_not_found"
