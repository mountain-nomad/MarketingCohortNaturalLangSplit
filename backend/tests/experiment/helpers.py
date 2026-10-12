"""Experiment API test helpers: an executor that also answers the export column query,
split/download shortcuts and a ZIP reader."""

import csv
import io
import json
import re
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from cohortsplit.compiler.validator import validate_select
from cohortsplit.experiment.models import ExperimentRunMember
from cohortsplit.warehouse.models import QueryResult
from tests.auth.helpers import ApiClient, FakeClock
from tests.cohort.helpers import (
    PREVIEW,
    FakeExecutor,
    Statement,
    confirm,
    interpret_ok,
    marketer,
)

RUNS = "/api/runs"
KEY = "push_bf_2026"
PHONE = "public.users.phone"
FIRST_NAME = "public.users.first_name"
EXPORTER_PERMISSIONS = ("cohort.create", "cohort.export")
_SELECTED = re.compile(r'u\."((?:[^"]|"")+)" AS "c\d+"')


@dataclass
class ExportExecutor(FakeExecutor):
    """FakeExecutor that also answers ``member_columns_query`` (the export column fetch).

    ``warehouse_users`` are the users that still exist in the warehouse (default: the
    current members).
    """

    warehouse_users: set[object] | None = None
    column_error: Exception | None = None
    column_statements: list[Statement] = field(default_factory=list)

    def execute(
        self, sql: str, params: Mapping[str, object] | None = None, *, row_cap: int | None = None
    ) -> QueryResult:
        if not sql.startswith('SELECT u."'):
            return super().execute(sql, params, row_cap=row_cap)
        statement = Statement(sql, params, row_cap)
        self.statements.append(statement)
        self.column_statements.append(statement)
        if self.allowed_tables:
            validate_select(sql, allowed_tables=self.allowed_tables)
        if self.column_error is not None:
            raise self.column_error
        names = [n.replace('""', '"') for n in _SELECTED.findall(sql)]
        present = set(self.members) if self.warehouse_users is None else self.warehouse_users
        rows = tuple(
            (user, *(self.columns.get(f"public.users.{n}", {}).get(user) for n in names))
            for user in (params or {}).values()
            if user in present
        )
        if row_cap is not None:
            assert len(rows) <= row_cap
        return QueryResult(
            columns=("canonical_user_id", *(f"c{i}" for i in range(1, len(names) + 1))),
            rows=rows,
        )


def exporter(
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    *,
    email: str = "marketer@example.com",
    columns: Sequence[str] = (),
    permissions: Sequence[str] = EXPORTER_PERMISSIONS,
) -> ApiClient:
    return marketer(app, db, clock, email=email, permissions=permissions, export_columns=columns)


def prepare(client: ApiClient, export_columns: Sequence[str] = ()) -> str:
    """Interpret + confirm + preview; the prepared cohort id."""
    interpreted = interpret_ok(client)
    response = client.post(PREVIEW, json=confirm(interpreted, export_columns=list(export_columns)))
    assert response.status_code == 200, response.text
    prepared: str = response.json()["prepared_cohort_id"]
    assert prepared
    return prepared


def split_body(prepared_id: str, **extra: Any) -> dict[str, Any]:
    return {"prepared_cohort_id": prepared_id, "experiment_key": KEY, **extra}


def split(client: ApiClient, prepared_id: str, **extra: Any) -> httpx.Response:
    return client.post(RUNS, json=split_body(prepared_id, **extra))


def split_ok(client: ApiClient, **extra: Any) -> dict[str, Any]:
    response = split(client, prepare(client), **extra)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def download(client: ApiClient, run_id: str, **body: Any) -> httpx.Response:
    return client.post(f"{RUNS}/{run_id}/download", json=body)


@dataclass
class Export:
    names: list[str]
    manifest: dict[str, Any]
    test: list[list[str]]
    control: list[list[str]]

    def ids(self, group: str) -> list[str]:
        rows = self.test if group == "test" else self.control
        return [row[0] for row in rows[1:]]


def read_export(response: httpx.Response, key: str = KEY) -> Export:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()

        def rows(name: str) -> list[list[str]]:
            return list(csv.reader(io.StringIO(archive.read(name).decode("utf-8"))))

        return Export(
            names=names,
            manifest=json.loads(archive.read("manifest.json")),
            test=rows(f"{key}_test.csv"),
            control=rows(f"{key}_control.csv"),
        )


def snapshot_of(db: Session, run_id: str) -> dict[str, str]:
    """The stored membership snapshot: canonical user id text -> group."""
    db.expire_all()
    rows = db.execute(
        select(ExperimentRunMember.canonical_user_id, ExperimentRunMember.assigned_group).where(
            ExperimentRunMember.run_id == run_id
        )
    ).all()
    return {str(user): str(group) for user, group in rows}
