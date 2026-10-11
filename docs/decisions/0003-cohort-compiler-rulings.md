# 0003 — Cohort compiler rulings

Status: accepted (feature/cohort-compiler) · Date: 2026-10-11

Decisions taken while implementing FR-4..FR-7 and FR-10 where the spec left room. Each picks the fail-closed or most conservative option.

## C1. Cohort spec v1 shape

`cohortsplit.cohort_spec.v1.CohortSpecV1` — `{"spec_version": "1", "entity": "user", "where": <condition> | omitted}`; strict (`extra="forbid"`), depth ≤ 8, ≤ 50 children per group, ≤ 200 nodes.

- `attribute` (filter on a canonical-user-table column), `attribute_time_window`, `related` (`table` = target, optional `path` of FK hops from the user table, `filters` on any path table, `time_window`, `aggregate`), `metric` (same over a business-context metric term), `all_of` / `any_of` / `not`.
- Filters: `= != > >= < <= in not_in is_null is_not_null` with literals (shared `Filter` with business context).
- Windows: `relative` (`last_days`, against the bound execution time in UTC), `absolute` (`[start, end)`, naive → UTC), `term` (a business-context `time_window`). A window without a column uses the metric's `time_column`; otherwise it is refused.
- Aggregates count/sum **distinct target rows** (no join fan-out); a missing sum is 0.
- No sequence node, no SQL field (BR-6, FR-4). `path` may be omitted only when exactly one shortest FK path exists; ties are refused ("ambiguous; give an explicit path") rather than guessed.
- Spec hash = SHA-256 of the canonical JSON (sorted keys, `exclude_none`).

## C2. draft-0 migration

Both: the crawler and the review API now write v1; Alembic `0005_cohort_compiler` rewrites stored `draft-0` rows to v1 (recomputing `referenced_columns` so AC-26 flagging keeps working) and keeps the originals in `spec_draft0_backup` so the downgrade restores them exactly; rows written or edited as v1 after the upgrade have no draft-0 form and become `needs_rereview` on downgrade (a backup is restored only when the row is still its untouched upgrade). `load_spec()` also upgrades `draft-0` on read (rows written by an older binary during a rolling upgrade). Upgrade: `entity` dropped (the canonical user id defines it), `related.table` = the first hop's table, windows get `kind: relative`. `draft.py` and its tests stay as the migration source format.

## C3. SQL validator policy

`compiler/validator.py`, independent of the compiler and the LLM:

- exactly one statement; no `;`, comments or NUL; psycopg named parameters only (`$1` or a stray `%` refused);
- root is a plain `SELECT`; **allowlist of AST node types** (select/from/join/where/order/limit/distinct, columns, literals, comparisons, `AND/OR/NOT/IN/IS/EXISTS`, `+ - *`, `CAST`, `INTERVAL`, `COUNT/SUM/COALESCE`, subqueries). Everything else is refused, which covers DML/DDL/DCL, `WITH` (also CTEs with DML), `SELECT … INTO`, `COPY`, unparsed commands (`REVOKE`, `VACUUM`, `SET`, `LOCK`, `DO`, `CALL`, `LISTEN`), locking clauses, set operations, window functions, table functions and every anonymous function (`pg_sleep`, `dblink`, `lo_*`, `set_config`, `nextval`, `pg_read_file`, …);
- casts only to `TIMESTAMPTZ` (no other type input functions); joins only inner with an `ON` condition (no comma or cross joins); every table schema-qualified and in the allowlist = the tables of the snapshot (the latest crawl); schema-qualified column references refused.

A validation failure of compiler output is a defect: 500 `sql_validation_failed`, logged, never executed. The database-level read-only role remains the last line.

## C4. Prepared cohort interface

Table `prepared_cohorts` (definition only): id, interpretation id, creator, created/expires (default TTL 24 h), NL request, spec, spec hash, SQL, definition params, SQL hash, semantic version, dialect, `as_of`, row count, selected export columns. **No membership, rows or export values.** Empty cohorts produce no prepared cohort (split/export disabled). Python API for `feature/experiment-split`: `PreparedCohortStore().get(db, id, user_id=, now=)` (owner only, unexpired) and `execute_membership(executor, prepared, allowed_tables=)` (re-validates and re-runs the stored SQL with the stored `as_of` through the read-only executor).

## C5. Interpretations are server-side and owner-bound

Interpret stores the spec, spec hash, semantic version and NL request (TTL 60 min) and returns its id. Preview must name that id and repeat the spec hash and semantic version (409 `confirmation_mismatch` otherwise), so a run is always tied to the NL request that produced its spec; another user's or an expired interpretation is 404. If the current snapshot's semantic version differs, 409 `semantic_version_changed`.

## C6. Execution time

Relative windows bind `%(as_of)s` = request time in UTC; the SQL text does not change with time (AC-10) and `sql_hash` excludes `as_of`. `as_of` is stored with the prepared cohort.

## C7. Deterministic pre-guards

Before the LLM: imperative mutation verbs (first word after politeness prefixes) or SQL-like write phrases → `mutation`; question openers (what/how/why/when/where/compare/calculate…, and is/are/did/has/total/… only when the request ends with `?`) → `analytics`; "create/make/build/add" a cohort/segment/audience/list is not a mutation; ordering words (`then`, `followed by`, `afterwards`, `within N days after …`) → `unsupported_construct`. The LLM's own rejections are honoured too; the spec model and the validator remain backstops.

## C8. Unknown terms and values

Undefined or stale business terms (metric / time window) → clarification naming them and pointing to Business context. A term of the wrong kind is named likewise. Values inside reviewed business definitions (metric and exclusion filters) are trusted: never value-checked or looked up — a defined value with no rows matches nothing. Literal values the marketer's spec adds on columns with a known domain (CHECK values, else the complete low-cardinality sample) must be in it → clarification with suggestions. Values on string or integer columns without a known domain (product/category names, ids) are looked up at preview with a validated `SELECT DISTINCT … IN (…)` before the cohort query → 422 `unknown_value` naming them. Invented tables, columns or FK hops → rejection `unknown_term` naming them.

## C9. Export columns in preview

The canonical id is always shown. Extra columns must be allowed by `PolicyService.effective_export_columns` (i.e. `cohort.export` + a role grant; 403 `export_column_denied`, audited) and must belong to the canonical user table (422 `export_column_unsupported`; one row per user).

## C10. Two-valued logic and exclusions

Attribute comparisons are `COALESCE(…, FALSE)`: a NULL never satisfies a comparison and `NOT` inverts exactly (users with an unknown country are "not from DE"). `!=` and `not_in` compile as `NOT COALESCE(= / IN, FALSE)`, so `country ≠ DE` and `NOT country = DE` select the same users, everywhere (attributes and related filters). The executor pins `TimeZone=UTC` per transaction so windows on `date`/`timestamp` columns do not depend on the server's zone. Business-context `exclusion` entries apply to every cohort as `NOT EXISTS`; an exclusion that cannot be joined refuses the cohort.

## C11. Audit and logs

`cohort.interpret` (non-sensitive; NL request, kind, reason, spec hash, semantic version; rejections recorded as `denied`) and `cohort.run` (**sensitive**, same transaction as the prepared cohort: NL request, spec hash, SQL hash, semantic version, row count, export columns; failures recorded as `error`/`denied`). Operational logs carry request id, kind, hashes, version and row count — never the request text or values. Raw LLM output is logged for debugging with credential-like strings scrubbed.

## C12. LLM provider

One `httpx` OpenAI-compatible client (`/chat/completions`, temperature 0, optional `response_format: json_object`); errors name the base URL without credentials/query and never echo the provider's error body. Schema violations are retried with the validation error fed back, up to `COHORTSPLIT_LLM_MAX_ATTEMPTS` (3), then 502 `llm_invalid_output`.
