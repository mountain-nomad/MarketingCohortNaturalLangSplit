# 0004 — Experiment split rulings

Status: accepted (feature/experiment-split) · Date: 2026-10-12

Decisions taken while implementing FR-8, FR-9 and FR-9a where the spec left room. Each picks the fail-closed or most conservative option.

## E1. Assignment algorithm (version 1)

`cohortsplit.experiment.assignment`:

```text
message = UTF-8(experiment_key) || 0x00 || UTF-8(canonical_id_text(user_id))
bucket  = unsigned big-endian integer of the first 8 bytes of SHA-256(message) mod 10,000
group   = "test" if bucket < test_percent * 100 else "control"
```

- `canonical_id_text`: integers as decimal (`42`), strings as stored, UUIDs in canonical lowercase form. Booleans, floats, bytes, empty strings and other types are refused (fail closed, 500 `unsupported_user_id`).
- The key alphabet (E2) excludes NUL, so the separator is unambiguous. The modulo bias of a 64-bit integer over 10,000 buckets is below 1e-15.
- No runtime `hash()`, no randomness: the group depends only on key, user id and allocation (BR-5). Proven across processes with different `PYTHONHASHSEED`.
- Golden vectors (`tests/unit/experiment/test_assignment.py`), computed independently from the text above:

  | key | user id | bucket | 50/50 | 90/10 |
  |---|---|---|---|---|
  | `push_black_friday_2026` | `1` | 8125 | control | test |
  | `push_black_friday_2026` | `4` | 4720 | test | test |
  | `push_black_friday_2026` | `6` | 374 | test | test |
  | `push_black_friday_2026` | `13` | 245 | test | test |
  | `push_black_friday_2026` | `1000000` | 9249 | control | control |
  | `aa_check_q1` | `7` | 3283 | test | test |
  | `Exp-1.v2` | `user-abc` | 5911 | control | test |

- A different algorithm must be a new `ASSIGNMENT_VERSION`; runs store the version they were made with.

## E2. Experiment key

Trimmed, then `^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?$` (1–100 ASCII letters/digits with inner `_ - .`). The key becomes file names (`<key>_test.csv`), so slashes, backslashes, spaces, quotes, control characters and non-ASCII letters are refused (422), and it cannot start or end with `.` or `-`. Keys are case-sensitive (they are hashed as typed). Reusing a key for another cohort is allowed (BR-5). The ZIP is named `cohortsplit_<key>.zip` so reserved Windows device names (`CON`, `NUL`…) never form a whole file name.

## E3. Semantic version and canonical id at split

The split re-reads the semantic snapshot. If its version differs from the prepared cohort's, the split is refused with 409 `semantic_version_changed` (interpret again) before the warehouse is touched; a missing canonical user id is 409 `canonical_user_id_missing`. Membership is **re-executed** from the prepared SQL with its stored `as_of` (`execute_membership`: AST re-validation, read-only executor, row cap, timeout), so the run reflects the data at split time; the response reports `preview_count` next to the actual counts.

## E4. Run visibility: 404, never 403

`GET /api/runs` and `GET /api/runs/{id}` need `cohort.create` **or** `cohort.read_all` (FR-A6 History tab). A run is visible to its creator and to holders of `cohort.read_all`. Anything else — unknown id, malformed id, someone else's run — is the same 404 `run_not_found`, so ids do not leak. Download needs `cohort.export` **and** visibility; a `cohort.read_all` + `cohort.export` holder can download others' runs, always under their *own* current column grants. The owner keeps download access after losing `cohort.create` (still needs `cohort.export`).

## E5. CSV formula-injection guard

Text cells (and header names) starting with `=`, `+`, `-`, `@`, TAB or CR are prefixed with `'`. Numbers, booleans, dates and integer user ids are rendered as values and never prefixed (a negative number is not a formula). Consequence: a phone number such as `+4915…` is exported as `'+4915…`; the manifest states the rule (`export.csv_formula_escaping`), and consumers that need the raw value strip one leading `'`. UTF-8, RFC 4180 quoting, header row, one row per user.

## E6. Export package

ZIP entries in order: `<key>_test.csv`, `<key>_control.csv`, `manifest.json`. The manifest (`format: cohortsplit-export/1`) holds the NL request, spec, spec hash, SQL, SQL parameters, SQL hash, `as_of`, semantic version, experiment key/type/allocation (+ A/A or A/B note), assignment algorithm and version, per-group counts, created at/by, and the export block (exported at/by, re-download flag, canonical id column, columns, users missing per group, CSV rule). The CSV header uses bare column names (`user_id,phone`). Download is `POST /api/runs/{id}/download` (CSRF), not GET: every download is an audited, warehouse-touching action. Body `columns: null` = the columns chosen at split time, `[]` = canonical id only. The ZIP is built completely (spooled: 8 MB in memory, then a temporary file) before the response starts, so a failure is an error response, never a partial ZIP.

## E7. Extra-column SQL

Built in `experiment/export.py` from the snapshot's canonical user table and key plus validated column names only — never from the client: `SELECT u."<key>" AS "canonical_user_id", u."<col>" AS "c1"… FROM "<schema>"."<table>" AS u WHERE u."<key>" IN (%(p1)s, …)`. Identifiers are quoted with the compiler's `quote_ident`; ids are bound parameters (integers when the run's ids were integers). Batches of 1,000 ids, `row_cap` = batch size, statement timeout of the read-only executor, and every distinct statement passes the compiler's AST validator with the snapshot's table allowlist. Requested columns must be columns of the canonical user table in the current snapshot (422 `export_column_unsupported`, also for a column removed from the warehouse).

## E8. Audit

- `cohort.split` (new, **sensitive**): written in the run's transaction (fail closed → 503 `audit_unavailable`, nothing stored). Metadata: prepared cohort id, NL request, spec/SQL hashes, semantic version, key, type, allocation, assignment version, export columns, per-group counts. Refused splits are recorded detached with `outcome` `error` (or `denied` for column refusals, sensitive) and an error code.
- Exports go through `PolicyService.authorize_export` immediately before data is produced: `cohort.export` for the first download of a run (by anyone), `cohort.redownload` afterwards; metadata run id, columns, per-group row counts. Denials are audited there; a failed success write refuses the export (AC-A21). A warehouse failure after authorization adds an `error` event with the code.
- Events and logs never contain member ids or exported values (tested).

## E9. Run storage

`experiment_runs` keeps its own copy of the definition (prepared cohorts expire after 24 h), the canonical id reference, the id type, assignment parameters, counts, the split-time export columns and a download counter. `experiment_run_members` holds `(run_id, position, canonical_user_id, assigned_group)` and nothing else (AC-21c); `position` keeps the membership query's order. `created_by` is `ON DELETE RESTRICT` (users are never hard-deleted). Runs are never re-executed, edited or deleted through the API.

## E10. Zero-user group, empty cohort

A run whose test or control group is empty is stored and returned with an `empty_group` warning and `requires_empty_group_confirmation: true`; every download (including re-downloads) of such a run needs `confirm_empty_group: true` (409 `empty_group_confirmation_required` otherwise). An empty cohort at split time is refused (422 `cohort_empty`) and nothing is stored.

## E11. Re-download edge cases

Membership and groups always come from the snapshot. Users no longer in the warehouse keep their row with empty extra cells and are counted per group in `export.missing_from_warehouse` (`null` when no extra column was requested, i.e. nothing was checked). If the canonical user id was reconfigured since the run, extra columns are refused (409 `canonical_user_id_changed`); the id-only export still works.

## E12. A/A

Same assignment machinery; `experiment_type: "aa"` is stored, shown in History and stated in the manifest ("treat both groups identically"). Groups are identical to an A/B run with the same key and allocation.

## E13. `/api/me` export columns

`export_columns` lists the extra columns the caller may export now (`cohort.export` + role grants; admins have no implicit grants). It only drives the split step's checkboxes; the server re-checks at split and at every download.

## E14. Demo LLM stub

`cohortsplit demo-llm-stub` (compose profile `demo`) is an opt-in, deterministic OpenAI-compatible server for the README walkthrough: it answers the documented demo requests with fixed specs and everything else with a clarification. It is never the default provider and grants nothing: its specs go through the same resolution, compilation, validation and read-only execution as a real model's.
