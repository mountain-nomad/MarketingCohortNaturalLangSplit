"""Audit of splits, exports and re-downloads (FR-A5, AC-A20, AC-A21, ruling E8).

A split is recorded as ``cohort.split`` in the run's transaction (sensitive). Exports and
re-downloads are recorded by the export gate before any data is produced; if that write
fails, nothing is exported. Events carry ids, columns and counts, never row values.
"""

import json
from typing import Any

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cohortsplit.experiment.models import ExperimentRun
from tests.auth.helpers import FakeClock, error_code
from tests.cohort.helpers import install_environment
from tests.experiment.helpers import (
    KEY,
    PHONE,
    ExportExecutor,
    download,
    exporter,
    prepare,
    read_export,
    split,
    split_ok,
)

MEMBERS = [900001, 900002, 900003, 900004, 900005]
PHONES = {m: f"+77012{m}" for m in MEMBERS}


def executor() -> ExportExecutor:
    return ExportExecutor(members=list(MEMBERS), columns={PHONE: dict(PHONES)})


def runs(db: Session) -> int:
    db.expire_all()
    return int(db.scalar(select(func.count()).select_from(ExperimentRun)) or 0)


def test_split_is_audited_with_key_allocation_and_counts(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: Any
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    install_environment(app, executor=executor())
    run = split_ok(client, allocation={"test": 60, "control": 40}, export_columns=[PHONE])

    [event] = audit_events.of("cohort.split")
    assert event.outcome == "success"
    assert event.actor_type == "user"
    assert event.target_type == "experiment_run"
    assert event.target_id == run["id"]
    metadata = event.metadata_
    assert metadata["experiment_key"] == KEY
    assert metadata["experiment_type"] == "ab"
    assert metadata["allocation"] == {"test": 60, "control": 40}
    assert metadata["counts"] == run["counts"]
    assert metadata["spec_hash"] == run["spec_hash"]
    assert metadata["sql_hash"] == run["sql_hash"]
    assert metadata["semantic_version"] == run["semantic_version"]
    assert metadata["export_columns"] == [PHONE]
    assert metadata["assignment_version"] == 1
    assert metadata["prepared_cohort_id"]


def test_refused_split_is_audited(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: Any
) -> None:
    client = exporter(app, db, clock)
    env = install_environment(app, executor=executor())
    prepared = prepare(client)
    env.executor.members = []

    assert split(client, prepared).status_code == 422

    [event] = audit_events.of("cohort.split")
    assert event.outcome == "error"
    assert event.metadata_["error"] == "cohort_empty"


def test_split_refused_when_audit_store_down(
    app: FastAPI, db: Session, clock: FakeClock, request: Any
) -> None:
    client = exporter(app, db, clock)
    install_environment(app, executor=executor())
    prepared = prepare(client)

    request.getfixturevalue("audit_store_down")
    response = split(client, prepared)

    assert response.status_code == 503
    assert error_code(response) == "audit_unavailable"
    assert runs(db) == 0


def test_export_and_redownload_are_audited_with_run_columns_and_counts(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: Any
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    install_environment(app, executor=executor())
    run = split_ok(client, export_columns=[PHONE])

    read_export(download(client, run["id"]))
    read_export(download(client, run["id"], columns=[]))

    [export] = audit_events.of("cohort.export")
    [redownload] = audit_events.of("cohort.redownload")
    assert export.outcome == "success"
    assert export.target_type == "cohort_run"
    assert export.target_id == run["id"]
    assert export.metadata_["columns"] == [PHONE]
    assert export.metadata_["row_counts"] == {
        "test": run["counts"]["test"],
        "control": run["counts"]["control"],
    }
    assert redownload.outcome == "success"
    assert redownload.metadata_["columns"] == []


def test_export_refused_when_audit_store_down(
    app: FastAPI, db: Session, clock: FakeClock, request: Any
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    env = install_environment(app, executor=executor())
    run = split_ok(client, export_columns=[PHONE])

    request.getfixturevalue("audit_store_down")
    response = download(client, run["id"])

    assert response.status_code == 503
    assert error_code(response) == "audit_unavailable"
    assert response.headers["content-type"].startswith("application/json")
    assert env.executor.column_statements == []


def test_audit_events_never_contain_member_ids_or_exported_values(
    app: FastAPI, db: Session, clock: FakeClock, audit_events: Any
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    install_environment(app, executor=executor())
    run = split_ok(client, export_columns=[PHONE])
    read_export(download(client, run["id"]))
    read_export(download(client, run["id"]))

    dumped = json.dumps([e.metadata_ for e in audit_events.all()])
    for member in MEMBERS:
        assert str(member) not in dumped
    assert "+77012" not in dumped


def test_logs_never_contain_member_ids_or_exported_values(
    app: FastAPI, db: Session, clock: FakeClock, caplog: Any
) -> None:
    client = exporter(app, db, clock, columns=[PHONE])
    install_environment(app, executor=executor())
    with caplog.at_level("DEBUG"):
        run = split_ok(client, export_columns=[PHONE])
        read_export(download(client, run["id"]))

    assert "experiment" in caplog.text  # the split and export were logged
    for member in MEMBERS:
        assert str(member) not in caplog.text
    assert "+77012" not in caplog.text
