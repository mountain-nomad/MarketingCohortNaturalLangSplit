"""Export format (FR-9): ZIP layout, CSV rendering, manifest and the extra-column query.

* ZIP: ``<key>_test.csv``, ``<key>_control.csv``, ``manifest.json`` (in that order).
* CSV: UTF-8, RFC 4180 quoting, header row, one row per user. Text cells starting with
  ``=``, ``+``, ``-``, ``@``, TAB or CR get a leading ``'`` so spreadsheets do not run
  them as formulas (ruling E5). Numbers, booleans and dates are rendered as such.
* Extra columns are fetched with SQL built here from the canonical user table and
  validated column names only; ids are bound parameters (ruling E7). Callers validate
  it with :func:`cohortsplit.compiler.validator.validate_select` before execution.
"""

import csv
import io
import json
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import IO, Any

from cohortsplit.compiler.resolve import JsonScalar
from cohortsplit.compiler.sql import ID_COLUMN, CompiledQuery, quote_ident
from cohortsplit.experiment.assignment import ASSIGNMENT_VERSION, BUCKETS
from cohortsplit.experiment.records import RunRecord

MANIFEST_FORMAT = "cohortsplit-export/1"
MANIFEST_NAME = "manifest.json"
GROUPS = ("test", "control")
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
CSV_ESCAPING_RULE = (
    "Text cells starting with =, +, -, @, TAB or CR are prefixed with ' "
    "(spreadsheet formula-injection guard); strip one leading ' to recover the raw value."
)
ASSIGNMENT_ALGORITHM = (
    "bucket = first 8 bytes of SHA-256(UTF-8(experiment_key) || 0x00 || "
    "UTF-8(canonical_user_id)) as an unsigned big-endian integer mod 10000; "
    "group = test if bucket < test_percent * 100, else control"
)
AA_NOTE = (
    "A/A check: treat both groups identically (same campaign or none); any difference "
    "between them measures noise, not an effect."
)
AB_NOTE = "A/B test: send the campaign to the test group only; control is the holdout."
USER_ALIAS = "u"


def escape_cell(value: object) -> str:
    """One CSV cell as text, with the formula-injection guard for text values."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float | Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    text = value if isinstance(value, str) else str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def csv_name(experiment_key: str, group: str) -> str:
    return f"{experiment_key}_{group}.csv"


def zip_name(experiment_key: str) -> str:
    return f"cohortsplit_{experiment_key}.zip"


def member_columns_query(
    user_table: str, user_key: str, columns: Sequence[str], ids: Sequence[JsonScalar]
) -> CompiledQuery:
    """``SELECT`` the canonical id and ``columns`` (bare names) of the given users."""
    if not ids:
        raise ValueError("member_columns_query needs at least one id")
    schema, table = user_table.split(".", 1)
    selected = [f"{USER_ALIAS}.{quote_ident(user_key)} AS {quote_ident(ID_COLUMN)}"] + [
        f"{USER_ALIAS}.{quote_ident(name)} AS {quote_ident(f'c{i}')}"
        for i, name in enumerate(columns, start=1)
    ]
    params: dict[str, JsonScalar] = {f"p{i}": value for i, value in enumerate(ids, start=1)}
    placeholders = ", ".join(f"%({name})s" for name in params)
    sql = (
        f"SELECT {', '.join(selected)} "  # noqa: S608 (quoted identifiers, bound values)
        f"FROM {quote_ident(schema)}.{quote_ident(table)} AS {USER_ALIAS} "
        f"WHERE {USER_ALIAS}.{quote_ident(user_key)} IN ({placeholders})"
    )
    return CompiledQuery(sql, params)


def build_manifest(
    run: RunRecord,
    *,
    exported_at: datetime,
    exported_by: int,
    redownload: bool,
    columns: Sequence[str],
    missing: Mapping[str, int] | None,
) -> dict[str, Any]:
    aa = run.experiment_type == "aa"
    return {
        "format": MANIFEST_FORMAT,
        "run_id": run.id,
        "nl_request": run.nl_request,
        "spec": dict(run.spec),
        "spec_hash": run.spec_hash,
        "sql": run.sql,
        "sql_params": dict(run.sql_params),
        "sql_hash": run.sql_hash,
        "dialect": run.dialect,
        "as_of": run.as_of.isoformat(),
        "semantic_version": run.semantic_version,
        "experiment": {
            "key": run.experiment_key,
            "type": "A/A" if aa else "A/B",
            "note": AA_NOTE if aa else AB_NOTE,
            "allocation": {"test": run.allocation.test, "control": run.allocation.control},
        },
        "assignment": {
            "version": run.assignment_version,
            "algorithm": ASSIGNMENT_ALGORITHM,
            "buckets": BUCKETS,
            "current_version": ASSIGNMENT_VERSION,
        },
        "counts": {"test": run.test_count, "control": run.control_count, "total": run.total},
        "created_at": run.created_at.isoformat(),
        "created_by": run.created_by,
        "export": {
            "exported_at": exported_at.isoformat(),
            "exported_by": exported_by,
            "redownload": redownload,
            "canonical_user_id": run.canonical_user_id,
            "columns": list(columns),
            "missing_from_warehouse": dict(missing) if missing is not None else None,
            "csv_formula_escaping": CSV_ESCAPING_RULE,
        },
        "files": {group: csv_name(run.experiment_key, group) for group in GROUPS},
    }


class ZipExport:
    """Writes the export ZIP into ``fileobj`` group by group (bounded memory)."""

    def __init__(self, fileobj: IO[bytes], experiment_key: str) -> None:
        self._key = experiment_key
        self._zip = zipfile.ZipFile(fileobj, "w", compression=zipfile.ZIP_DEFLATED)

    def write_group(
        self, group: str, header: Sequence[str], rows: Iterable[Sequence[object]]
    ) -> int:
        """Write ``<key>_<group>.csv``; returns the number of data rows."""
        count = 0
        with self._zip.open(csv_name(self._key, group), "w") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            writer = csv.writer(text)
            writer.writerow([escape_cell(h) for h in header])
            for row in rows:
                writer.writerow([escape_cell(cell) for cell in row])
                count += 1
            text.flush()
            text.detach()
        return count

    def write_manifest(self, manifest: Mapping[str, Any]) -> None:
        self._zip.writestr(
            MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)
        )

    def close(self) -> None:
        self._zip.close()
