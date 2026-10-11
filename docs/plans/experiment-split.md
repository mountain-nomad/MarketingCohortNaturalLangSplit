# Experiment Split Implementation Plan

Branch: `feature/experiment-split` · MVP Feature Breakdown row 6 · Implements **FR-8** (deterministic test/control split), **FR-9** (export ZIP with manifest), **FR-9a** (run history and re-download), business rule **BR-5**, steps 6–8 of the "Campaign" user flow, the split/export rows of *Edge Cases* and *Failure Behavior*, the **History** tab of `docs/product/authentication.md` FR-A6, column-grant enforcement at export and re-download (FR-A4) and the export/re-download audit of FR-A5. Acceptance criteria: **AC-14 … AC-21d**, **AC-29** (new endpoints), **AC-31**, and **AC-A15 … AC-A17**, **AC-A21**.

Depends on `feature/cohort-compiler` (`PreparedCohortStore`, `execute_membership`, `CohortEnvironment`, ruling C4/C9), `feature/authentication-rbac` (`require_permission`, `PolicyService.authorize_export` / `effective_export_columns`, `AuditService`) and `feature/semantic-context` (`SemanticContextProvider.snapshot()`: allowed tables, canonical user id).

## Problem

Preview leaves a prepared cohort definition behind, but nothing turns it into an experiment. The marketer cannot split the audience into a test group (receives the campaign) and a control group (holdout), download the groups, or later prove which users were in which group. The split must be reproducible (same key + user → same group, forever), the export must only ever contain columns the user's roles may export *at download time*, and every run must be kept as an auditable, PII-free record that can be downloaded again with identical membership.

## Desired behavior

1. After preview, a user with `cohort.create` configures the split: experiment key (required), allocation (test/control integer percentages, default 50/50), A/B or A/A, and optional extra export columns (only columns their roles are granted for export are offered).
2. The backend re-checks the semantic version (409 → re-interpret), re-executes the prepared cohort's membership through the read-only executor (row cap, timeout), assigns every user with the documented stable hash and stores a **run**: definition, SQL, semantic version, key, allocation, per-group counts, creator, timestamp and the **membership snapshot** (canonical user id + group only). The response shows the per-group counts; a group with 0 users produces a warning and later requires explicit confirmation to export.
3. A user with `cohort.export` downloads one ZIP: `<key>_test.csv`, `<key>_control.csv`, `manifest.json`. CSVs hold the canonical user id plus extra columns only if `authorize_export` allows them **at that moment**; extra columns are fetched from the warehouse at download time with SQL built here from validated identifiers.
4. The **History** tab lists the user's runs (everyone's with `cohort.read_all`). Re-download rebuilds the ZIP from the stored snapshot (identical membership), re-fetching extra columns under the user's **current** grants. Runs are never re-executed. Users that disappeared from the warehouse keep their row with empty extra columns and are counted in the manifest.

## Scope

Backend (`backend/src/cohortsplit/experiment/`):

| Module | Responsibility |
|---|---|
| `assignment.py` | Pure, versioned assignment: key validation, allocation model, canonical id text, `bucket`, `assign`, `split`. No I/O, no randomness, no runtime `hash()`. |
| `export.py` | Pure export format: CSV formula-injection escaping, cell rendering, member-column SQL builder (validated with the compiler's AST validator), manifest document, ZIP file names. |
| `models.py` | `experiment_runs`, `experiment_run_members` ORM models. |
| `store.py` | Run repository: create with members, visibility-filtered get/list, ordered member batches. |
| `service.py` | `ExperimentService.split / list_runs / get_run / download`: orchestration, audit, structured logs. |
| `errors.py`, `api.py`, `wiring.py` | `/api/runs` router and app state. |
| `llm/demo_stub.py` + CLI `cohortsplit demo-llm-stub` | Deterministic OpenAI-compatible stub for the README demo (AC-31 without an LLM); opt-in compose profile `demo`. |

Also: `/api/me` gains `export_columns` (the columns the caller may export now), audit action `cohort.split`, migration `0006_experiment_split`, README campaign walkthrough, rulings `docs/decisions/0004-experiment-split-rulings.md`.

Frontend: split step in `frontend/src/cohort/` after preview (key, allocation, A/A, granted columns, counts, zero-group confirmation, download) and `frontend/src/history/HistoryTab.tsx` replacing the placeholder.

## Non-goals

- Sample size / MDE / power calculator, result analysis (MVP non-goal; statsmodels wrapper stays for later).
- More than two groups; cohort refresh (re-executing a past run).
- Export columns outside the canonical user table (ruling C9 keeps one row per user).
- Push-provider integrations; asynchronous/background export jobs.
- Deleting or editing runs.

## Proposed interfaces

### Assignment (ruling E1, pinned by golden vectors)

```python
ASSIGNMENT_VERSION = 1
BUCKETS = 10_000
def validate_experiment_key(raw: str) -> str          # trimmed; ^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?$
class Allocation(BaseModel): test: int; control: int   # each 1..99, sum 100; default 50/50
def canonical_id_text(user_id: object) -> str         # int -> decimal, str -> as is, UUID -> canonical str; else TypeError
def bucket(experiment_key: str, user_id_text: str) -> int
    # SHA-256( UTF-8(key) || 0x00 || UTF-8(user_id_text) ); first 8 bytes as unsigned
    # big-endian integer; mod 10_000
def assign(experiment_key, user_id, allocation) -> Literal["test", "control"]
    # test  iff bucket < allocation.test * 100
def split(experiment_key, user_ids, allocation) -> Split(test: tuple, control: tuple)
```

### HTTP API (CSRF on unsafe methods, `{"error": {...}}` errors)

| Method & path | Permission | Body / query | Result |
|---|---|---|---|
| `POST /api/runs` | `cohort.create` | `{prepared_cohort_id, experiment_key, allocation: {test, control}, experiment_type: "ab"\|"aa", export_columns: []}` | 201 run summary `{id, experiment_key, experiment_type, allocation, counts: {test, control, total}, preview_count, warnings, requires_empty_group_confirmation, export_columns, created_at, created_by, semantic_version, …}`. 404 `prepared_cohort_not_found`; 409 `semantic_version_changed`; 422 `validation_error` / `cohort_empty` / `row_cap_exceeded` / `export_column_unsupported`; 403 `export_column_denied`; 504 / 503 / 502 warehouse errors; 503 `audit_unavailable`. |
| `GET /api/runs` | `cohort.create` or `cohort.read_all` | `?limit&offset` | `{items: [run summary + creator], total, limit, offset, scope: "own"\|"all"}` |
| `GET /api/runs/{run_id}` | `cohort.create` or `cohort.read_all` | — | run detail (definition, SQL, counts); 404 `run_not_found` for unknown **or invisible** runs |
| `POST /api/runs/{run_id}/download` | `cohort.export` | `{columns: [...] \| null, confirm_empty_group: false}` | `application/zip` attachment; 404 `run_not_found`; 409 `empty_group_confirmation_required` / `canonical_user_id_changed`; 403 `export_column_denied` (audited); 422 `export_column_unsupported`; warehouse errors; 503 `audit_unavailable` |

`columns: null` means "the columns chosen at split time". First download of a run is audited as `cohort.export`, every later one as `cohort.redownload`.

### ZIP layout

```text
<key>_test.csv      header: <canonical id column name>, <extra column names>; one row per user
<key>_control.csv
manifest.json       format, run id, NL request, spec, spec hash, SQL, SQL params, SQL hash, as_of,
                    semantic version, experiment key/type (+ A/A note), allocation, assignment
                    algorithm + version, per-group counts, created at/by, exported at, redownload,
                    columns, users missing from the warehouse per group, CSV escaping rule
```

## Data-model changes (`0006_experiment_split`, `down_revision = "0005_cohort_compiler"`)

| Table | Columns |
|---|---|
| `experiment_runs` | `id varchar(32) PK`, `created_by → users (RESTRICT)`, `created_at`, `prepared_cohort_id varchar(32)` (no FK: prepared cohorts expire), `nl_request`, `spec jsonb`, `spec_hash`, `sql`, `sql_params jsonb`, `sql_hash`, `dialect`, `as_of`, `semantic_version`, `canonical_user_id` (`schema.table.column`), `user_id_type` (`integer`\|`string`), `experiment_key`, `experiment_type` (`ab`\|`aa`), `test_percent`, `control_percent` (CHECK 1..99, sum 100), `assignment_version`, `test_count`, `control_count`, `export_columns jsonb`, `download_count`; index `(created_by, created_at)` |
| `experiment_run_members` | `run_id → experiment_runs (CASCADE)`, `position int`, `canonical_user_id text`, `assigned_group` (CHECK `test`\|`control`); PK `(run_id, position)`, UNIQUE `(run_id, canonical_user_id)` — **no other columns** (AC-21c) |

## Implementation steps

1. Commit this plan.
2. **Slice A – assignment** (RED → GREEN): golden vectors, validation, disjoint/union, distribution, restart, cross-cohort stability.
3. **Slice B – export format** (RED → GREEN): CSV escaping, cell rendering, member-column SQL, manifest, file names.
4. **Slice C – persistence, service, API, audit, migration, `/api/me` export columns** (RED → GREEN) on SQLite + PostgreSQL with fake executor.
5. **Slice D – integration** on the demo warehouse / scratch schema: full split → download → re-download, real column fetch, missing users, migration up/down.
6. **Slice E – demo LLM stub** (RED → GREEN) and compose `demo` profile.
7. **Slice F – frontend** split step and History tab (RED → GREEN).
8. README (AC-31 walkthrough), rulings, completion gate, container smoke test, review, fixes test-first, PR.

## Tests

### AC → test map

| AC | Test(s) |
|---|---|
| AC-14 | `tests/unit/experiment/test_assignment.py::test_assignment_identical_in_a_fresh_interpreter` (subprocess), `::test_golden_vectors`; `tests/experiment/test_split_api.py::test_split_twice_gives_identical_assignment`; `tests/cohort_reference/test_experiment_split_demo.py::test_split_identical_across_app_restart` |
| AC-15 | `test_assignment.py::test_same_user_same_group_in_different_cohorts`; `test_split_api.py::test_user_shared_by_two_cohorts_lands_in_same_group` |
| AC-16 | `test_assignment.py::test_groups_disjoint_and_union_is_cohort`; `test_split_api.py::test_snapshot_groups_disjoint_union_equals_membership` |
| AC-17 | `test_assignment.py::test_distribution_within_one_point[50-50]`, `[90-10]` (100,000 synthetic ids) |
| AC-18 | `test_assignment.py` key/allocation validation; `test_split_api.py::test_invalid_split_input_refused` (sum ≠ 100, group < 1 %, blank key, unsafe key) |
| AC-19 | `tests/experiment/test_download_api.py::test_ac19_phone_without_grant_refused` (direct API, export and re-download) |
| AC-20 | `test_download_api.py::test_ac20_granted_phone_exported_one_row_per_user` (+ preview NULL count covered by `tests/cohort/test_preview_api.py`) |
| AC-21 | `test_download_api.py::test_ac21_zip_layout_and_manifest`; `tests/unit/experiment/test_export_format.py::test_manifest_fields` |
| AC-21a | `test_download_api.py::test_ac21a_redownload_membership_identical_even_if_warehouse_changes`; demo integration `::test_full_campaign_split_download_redownload` |
| AC-21b | `test_download_api.py::test_ac21b_redownload_after_grant_revoked_refused`; demo integration |
| AC-21c | `test_split_api.py::test_ac21c_snapshot_has_only_id_and_group`; `tests/integration/test_migration_experiment_split.py::test_member_table_columns` |
| AC-21d | `tests/experiment/test_runs_api.py::test_ac21d_cannot_list_get_or_download_others_runs`, `::test_read_all_sees_everyone` |
| AC-29 | `tests/experiment/test_experiment_endpoint_security.py` (401, 403, CSRF per endpoint), route inventory in `tests/auth/test_endpoint_security.py` |
| AC-31 | README walkthrough + container smoke test (PR evidence); `tests/unit/llm/test_demo_stub.py` |
| AC-A15 | `test_download_api.py::test_ac19_phone_without_grant_refused` (denied audit event, export and re-download) |
| AC-A16 | `test_download_api.py::test_a16_grant_added_then_revoked` |
| AC-A17 | `test_download_api.py::test_a17_canonical_id_with_export_permission_only` |
| AC-A21 | `tests/experiment/test_experiment_audit.py::test_export_refused_when_audit_store_down`, `::test_split_refused_when_audit_store_down` |
| Audit | `test_experiment_audit.py` (split event metadata, export vs re-download, no row values in events) |
| Edge cases | `test_split_api.py` (empty cohort, zero group warning, row cap, version changed, expired/foreign prepared cohort), `test_download_api.py` (zero-group confirmation, missing users, canonical id changed, CSV injection) |
| Frontend | `frontend/src/cohort/SplitStep.test.tsx`, `frontend/src/history/HistoryTab.test.tsx` |

## Edge cases

Empty cohort at split (422 `cohort_empty`, nothing stored) · a group with 0 users (warning; download needs `confirm_empty_group`) · row cap exceeded at split (422 with cap) · timeout (504) · warehouse down (503, no partial export) · semantic version changed since preview (409, re-interpret) · prepared cohort expired / someone else's (404) · reused key with another cohort (allowed, BR-5) · key with path characters / spaces / unicode (422) · user deleted from the warehouse after the run (empty extra columns, counted) · canonical user id reconfigured after the run (extra columns refused 409; id-only export still works) · column removed from the warehouse (422 unsupported) · grant revoked after the run (403 at re-download) · CSV values starting with `= + - @ TAB CR` (escaped).

## Security implications

- Assignment is deterministic code, never the LLM; the hash is specified, versioned and pinned.
- Column grants are checked at every download through `authorize_export` (FR-A4); the denial is audited; the success event is written before any export data is produced and its failure refuses the export (AC-A21).
- Extra-column SQL is built here from the snapshot's canonical user table and the validated column names only (quoted identifiers, ids as bound parameters), AST-validated, run through the read-only executor. No SQL from the client.
- Runs are visible only to their creator or `cohort.read_all`; invisible runs are 404 (no existence leak).
- The membership snapshot holds canonical ids and groups only; audit events and logs hold ids/hashes/counts, never exported values.
- CSV formula injection is neutralised; the experiment key is restricted to a filename-safe alphabet.

## Acceptance criteria

AC-14 … AC-21d, AC-29 (new endpoints), AC-31, AC-A15 … AC-A17 and AC-A21 pass as mapped; the README-driven container smoke test (create-admin, crawl, business context, AC-2 request, split, download, re-download, grant revocation) succeeds.

## Definition of done

Tests-only `test:` commits (with RED summaries) precede the implementation commits that make them pass; `make lint`, `make typecheck`, `make test`, `make up` + `make test-integration`, alembic upgrade head → downgrade base → upgrade head pass with fresh output; smoke test done; independent review Critical/Important findings fixed test-first; README + rulings updated; PR open with CI green.
