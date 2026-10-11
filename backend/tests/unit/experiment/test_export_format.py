"""Export format: CSV formula-injection escaping, member-column SQL, ZIP layout and manifest
(FR-9, AC-21, rulings E5/E6)."""

import csv
import io
import json
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from cohortsplit.compiler.validator import SqlValidationError, validate_select
from cohortsplit.experiment.assignment import Allocation
from cohortsplit.experiment.export import (
    CSV_ESCAPING_RULE,
    MANIFEST_FORMAT,
    ZipExport,
    build_manifest,
    csv_name,
    escape_cell,
    member_columns_query,
    zip_name,
)
from cohortsplit.experiment.records import RunRecord

NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def run_record(**overrides: object) -> RunRecord:
    values: dict[str, object] = {
        "id": "f" * 32,
        "created_by": 7,
        "created_at": NOW,
        "prepared_cohort_id": "d" * 32,
        "nl_request": "Users from Germany who have an active cart",
        "spec": {"spec_version": "1", "entity": "user"},
        "spec_hash": "b" * 64,
        "sql": 'SELECT DISTINCT u."user_id" AS "canonical_user_id" FROM "public"."users" AS u',
        "sql_params": {"p1": "DE"},
        "sql_hash": "e" * 64,
        "dialect": "postgresql",
        "as_of": NOW,
        "semantic_version": "c" * 64,
        "canonical_user_id": "public.users.user_id",
        "user_id_type": "integer",
        "experiment_key": "push_bf",
        "experiment_type": "ab",
        "allocation": Allocation(test=90, control=10),
        "assignment_version": 1,
        "test_count": 2,
        "control_count": 1,
        "export_columns": ("public.users.phone",),
        "download_count": 0,
    }
    values.update(overrides)
    return RunRecord(**values)  # type: ignore[arg-type]


# -- CSV escaping (ruling E5) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=HYPERLINK("http://evil")', '\'=HYPERLINK("http://evil")'),
        ("+4915112345", "'+4915112345"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tcmd", "'\tcmd"),
        ("\rcmd", "'\rcmd"),
        ("plain", "plain"),
        ("a=b", "a=b"),
        ("", ""),
    ],
)
def test_text_cells_starting_with_formula_characters_are_escaped(value: str, expected: str) -> None:
    assert escape_cell(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (-5, "-5"),
        (42, "42"),
        (Decimal("-1.50"), "-1.50"),
        (2.5, "2.5"),
        (True, "true"),
        (False, "false"),
        (date(2026, 1, 2), "2026-01-02"),
        (NOW, "2026-03-02T09:00:00+00:00"),
    ],
)
def test_typed_cells_are_rendered_without_escaping(value: object, expected: str) -> None:
    assert escape_cell(value) == expected


def test_unknown_types_are_rendered_as_escaped_text() -> None:
    class Odd:
        def __str__(self) -> str:
            return "=1+1"

    assert escape_cell(Odd()) == "'=1+1"


def test_escaping_rule_is_documented() -> None:
    for marker in ("=", "+", "-", "@", "TAB", "CR", "'"):
        assert marker in CSV_ESCAPING_RULE


# -- extra-column SQL (ruling E7) --------------------------------------------------------


def test_member_columns_query_is_parameterized_and_valid() -> None:
    query = member_columns_query("public.users", "user_id", ["phone", "email"], [3, 1, 2])

    assert query.sql == (
        'SELECT u."user_id" AS "canonical_user_id", u."phone" AS "c1", u."email" AS "c2" '
        'FROM "public"."users" AS u WHERE u."user_id" IN (%(p1)s, %(p2)s, %(p3)s)'
    )
    assert dict(query.params) == {"p1": 3, "p2": 1, "p3": 2}
    validate_select(query.sql, allowed_tables={"public.users"})


def test_member_columns_query_ids_never_in_sql_text() -> None:
    query = member_columns_query("public.users", "user_id", ["phone"], ["1); DROP TABLE x; --"])

    assert "DROP" not in query.sql
    validate_select(query.sql, allowed_tables={"public.users"})


def test_member_columns_query_quotes_identifiers() -> None:
    query = member_columns_query("public.users", "user_id", ['odd"name'], [1])

    assert 'u."odd""name"' in query.sql


def test_member_columns_query_refuses_hostile_identifier_at_validation() -> None:
    query = member_columns_query("public.users", "user_id", ['x" FROM pg_shadow; --'], [1])

    with pytest.raises(SqlValidationError):
        validate_select(query.sql, allowed_tables={"public.users"})


def test_member_columns_query_needs_ids() -> None:
    with pytest.raises(ValueError, match="at least one"):
        member_columns_query("public.users", "user_id", ["phone"], [])


# -- file names and manifest -------------------------------------------------------------


def test_file_names() -> None:
    assert csv_name("push_bf", "test") == "push_bf_test.csv"
    assert csv_name("push_bf", "control") == "push_bf_control.csv"
    assert zip_name("push_bf") == "cohortsplit_push_bf.zip"


def test_manifest_fields() -> None:
    manifest = build_manifest(
        run_record(),
        exported_at=NOW,
        exported_by=9,
        redownload=True,
        columns=["public.users.phone"],
        missing={"test": 1, "control": 0},
    )

    assert manifest["format"] == MANIFEST_FORMAT
    assert manifest["run_id"] == "f" * 32
    assert manifest["nl_request"] == "Users from Germany who have an active cart"
    assert manifest["spec"] == {"spec_version": "1", "entity": "user"}
    assert manifest["spec_hash"] == "b" * 64
    assert manifest["sql"].startswith("SELECT DISTINCT")
    assert manifest["sql_params"] == {"p1": "DE"}
    assert manifest["sql_hash"] == "e" * 64
    assert manifest["as_of"] == "2026-03-02T09:00:00+00:00"
    assert manifest["semantic_version"] == "c" * 64
    assert manifest["experiment"]["key"] == "push_bf"
    assert manifest["experiment"]["type"] == "A/B"
    assert manifest["experiment"]["allocation"] == {"test": 90, "control": 10}
    assert manifest["assignment"]["version"] == 1
    assert "SHA-256" in manifest["assignment"]["algorithm"]
    assert manifest["counts"] == {"test": 2, "control": 1, "total": 3}
    assert manifest["created_at"] == "2026-03-02T09:00:00+00:00"
    assert manifest["created_by"] == 7
    assert manifest["export"]["exported_at"] == "2026-03-02T09:00:00+00:00"
    assert manifest["export"]["exported_by"] == 9
    assert manifest["export"]["redownload"] is True
    assert manifest["export"]["canonical_user_id"] == "public.users.user_id"
    assert manifest["export"]["columns"] == ["public.users.phone"]
    assert manifest["export"]["missing_from_warehouse"] == {"test": 1, "control": 0}
    assert manifest["export"]["csv_formula_escaping"] == CSV_ESCAPING_RULE
    assert manifest["files"] == {"test": "push_bf_test.csv", "control": "push_bf_control.csv"}
    json.dumps(manifest)  # serialisable


def test_aa_manifest_says_so() -> None:
    manifest = build_manifest(
        run_record(experiment_type="aa"),
        exported_at=NOW,
        exported_by=7,
        redownload=False,
        columns=[],
        missing=None,
    )

    assert manifest["experiment"]["type"] == "A/A"
    assert "identical" in manifest["experiment"]["note"].lower()
    assert manifest["export"]["missing_from_warehouse"] is None


# -- ZIP --------------------------------------------------------------------------------


def test_zip_layout_and_csv_content() -> None:
    buffer = io.BytesIO()
    export = ZipExport(buffer, "push_bf")
    written = export.write_group("test", ["user_id", "phone"], [[1, "+4915"], [3, None]])
    export.write_group("control", ["user_id", "phone"], [[2, "=cmd"]])
    export.write_manifest({"format": MANIFEST_FORMAT})
    export.close()

    assert written == 2
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        assert archive.namelist() == ["push_bf_test.csv", "push_bf_control.csv", "manifest.json"]
        test_rows = list(csv.reader(io.StringIO(archive.read("push_bf_test.csv").decode())))
        control_rows = list(csv.reader(io.StringIO(archive.read("push_bf_control.csv").decode())))
        manifest = json.loads(archive.read("manifest.json"))
    assert test_rows == [["user_id", "phone"], ["1", "'+4915"], ["3", ""]]
    assert control_rows == [["user_id", "phone"], ["2", "'=cmd"]]
    assert manifest == {"format": MANIFEST_FORMAT}


def test_zip_header_is_escaped_too() -> None:
    buffer = io.BytesIO()
    export = ZipExport(buffer, "k")
    export.write_group("test", ["=evil"], [])
    export.close()

    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        assert archive.read("k_test.csv").decode().splitlines() == ["'=evil"]
