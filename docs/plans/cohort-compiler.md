# Cohort Compiler Implementation Plan

Branch: `feature/cohort-compiler` · MVP Feature Breakdown row 5 · Implements **FR-4** (NL → structured spec), **FR-5** (interpretation preview & confirmation), **FR-6** (deterministic SQL compilation & validation), **FR-7** (cohort preview), **FR-10** (LLM provider), business rules **BR-1, BR-2, BR-3, BR-4, BR-6**, the compiler rows of *Edge Cases* and *Failure Behavior*, steps 1–5 of the "Campaign" user flow, the **New cohort** tab of `docs/product/authentication.md` FR-A6 and the cohort-run audit event of FR-A5. Acceptance criteria: **AC-1 … AC-11**, **AC-28**, **AC-29** (new endpoints) and **AC-30**.

Depends on `feature/warehouse-crawler` (ruling R1: migrate `draft-0` specs; `ReadOnlyExecutor`), `feature/authentication-rbac` (`require_permission`, `AuditService`, `PolicyService.effective_export_columns`) and `feature/semantic-context` (`SemanticContextProvider.snapshot()`, typed business context, rulings S1/S2/S6/S7).

## Problem

A marketer can describe an audience, but nothing turns the description into a cohort. The platform needs an LLM interpreter that produces a *typed* cohort specification from reviewed semantic context only, deterministic code that turns that spec into read-only SQL, an independent SQL safety check, and a preview the marketer confirms before anything else happens. The LLM must never be the execution or security authority.

## Desired behavior

1. A user with `cohort.create` types a request in the **New cohort** tab.
2. The backend takes **one** semantic snapshot, runs deterministic pre-guards (mutation / analytics / ordered-sequence wording), then asks the configured OpenAI-compatible LLM for a tagged result: `interpretation(spec)` | `clarification(question)` | `rejection(reason)`.
3. A returned spec is resolved **deterministically** against the snapshot: every table, column, join, business term, time-window term and enumerable value must exist. Undefined terms and unknown values become a clarification naming them; invented tables/columns/joins become a rejection naming them. Nothing is executed.
4. The user sees the readable interpretation (e.g. `has addresses with country_code = DE AND has carts with status = abandoned AND NOT has paid_order`), the spec, its hash and the semantic version, and confirms.
5. Preview re-checks the semantic version (409 if it changed → re-interpret), compiles byte-identical SQL, validates its AST, checks unknown lookup values, executes through `ReadOnlyExecutor` (timeout, row cap) and returns the total count, bounded sample rows (canonical ID + permitted export columns), the read-only SQL and NULL counts per selected export column. An empty cohort disables split/export; an over-cap cohort is refused with the cap.
6. The run is audited (NL request, spec hash, SQL hash, semantic version, row count) and a short-lived **prepared cohort** is stored for the experiment-split branch — never result rows or PII.

## Scope

Backend:

| Module | Responsibility |
|---|---|
| `cohort_spec/v1.py` | Cohort spec v1 (typed, versioned Pydantic), `referenced_columns`, `spec_hash`. Pure. |
| `cohort_spec/upgrade.py` | `draft-0` → v1 (and back, for the migration downgrade); `load_spec(raw)` on-read upgrader. Pure. |
| `compiler/resolve.py` | Semantic resolution against a `SemanticContextSnapshot`: references, FK joins, business terms, values, exclusions → `ResolvedCohort` or problems. Pure. |
| `compiler/readable.py` | Human-readable interpretation from a resolved cohort. Pure. |
| `compiler/sql.py` | Deterministic PostgreSQL compiler: membership, sample, NULL-count and value-lookup queries with bound parameters. Pure. |
| `compiler/validator.py` | sqlglot AST validation (single read-only SELECT, deny/allow lists, table allowlist). Pure. |
| `llm/settings.py`, `llm/client.py` | FR-10 OpenAI-compatible chat client (`httpx`), typed errors, no secrets in logs. |
| `llm/prompt.py` | Prompt from the snapshot only (BR-7). Pure. |
| `llm/interpreter.py` | Tagged LLM result schema, bounded retries, raw-output debug logging. |
| `cohort/guards.py` | Deterministic NL pre-guards (mutation, analytics, sequence). Pure. |
| `cohort/models.py`, `cohort/store.py` | `cohort_interpretations` and `prepared_cohorts` tables + repository. |
| `cohort/service.py` | `CohortService.interpret / preview`: orchestration, audit, structured logs. |
| `cohort/api.py`, `cohort/wiring.py` | `/api/cohorts/*` router; app state (LLM + executor factories). |

Also: crawler emits v1 use-case specs (`crawler/use_cases.py`), semantic service validates edited specs as v1, migration `0005_cohort_compiler`, settings + `.env.example` + compose LLM variables, audit action names, README, rulings `docs/decisions/0003-cohort-compiler-rulings.md`.

Frontend: `frontend/src/cohort/` New cohort tab replacing the placeholder.

## Non-goals

- Split, export ZIP, run history, membership snapshots (`feature/experiment-split`).
- Ordered sequences (BR-6) — explicit "not supported yet".
- Visual spec editing; multi-turn chat (the marketer rephrases).
- Export columns outside the canonical user table (one row per user is guaranteed only there).
- Dialects other than PostgreSQL (the compiler takes a `dialect` argument and refuses others).
- Streaming LLM responses, tool calling, MCP.

## Proposed interfaces

### Cohort spec v1 (ruling C1)

```python
SPEC_VERSION = "1"
Operator = Literal["=", "!=", ">", ">=", "<", "<=", "in", "not_in", "is_null", "is_not_null"]
class Filter:          column: QualifiedColumn; operator: Operator; value: Scalar | tuple[Scalar, ...] | None
class Join:            from_column: QualifiedColumn; to_column: QualifiedColumn   # one FK hop, outwards from the user table
class RelativeWindow:  kind="relative"; column: QualifiedColumn | None; last_days: int (1..36500)
class AbsoluteWindow:  kind="absolute"; column: QualifiedColumn | None; start: AwareDatetime | None; end: AwareDatetime | None  # UTC, [start, end)
class TermWindow:      kind="term"; term: str; column: QualifiedColumn | None        # business-context time_window ("recently")
class Aggregate:       function: "count" | "sum"; column: QualifiedColumn | None; operator: > >= = < <= !=; value: int | float >= 0
Condition =
  | AttributeCondition   type="attribute";        filter: Filter                       # column of the canonical user table
  | AttributeWindow      type="attribute_window"; window: TimeWindow                   # e.g. registered in 2024
  | RelatedCondition     type="related";          table; path: tuple[Join, ...] | None; filters; window; aggregate
  | MetricCondition      type="metric";           term: str; path; filters; window; aggregate   # business-context metric
  | AllOf / AnyOf        type="all_of" | "any_of"; conditions: tuple[Condition, ...] (1..50)
  | Not                  type="not";              condition: Condition
class CohortSpecV1:      spec_version="1"; entity="user"; where: Condition | None    # None = every user
```

`related`/`metric` = existence (or a count/sum threshold) of rows of the target table reached from the canonical user through `path`. `path` may be omitted when exactly one shortest FK path exists; it may extend past the target for filters on further tables (e.g. category name). Aggregates count/sum **distinct target rows** (no fan-out). There is no `sequence`/`then` node (BR-6) and no SQL anywhere. `extra="forbid"`, depth ≤ 8, ≤ 50 children.

### Resolution & compilation

```python
resolve(spec: CohortSpecV1, snapshot: SemanticContextSnapshot) -> Resolution
    # Resolution = ResolvedCohort | Unresolved(clarification: str, unknown_terms, unknown_values)
    #                             | Invalid(problems: tuple[str, ...])    -> rejection unknown_term
readable(resolved: ResolvedCohort) -> str
compile_cohort(resolved, *, dialect="postgresql") -> CompiledQuery(sql: str, params: Mapping[str, JsonScalar])
compile_sample(resolved, compiled, columns, limit) / compile_null_counts(...) / compile_value_lookup(...)
validate_select(sql: str, *, allowed_tables: frozenset[str]) -> None   # raises SqlValidationError(reason)
spec_hash(spec) -> str      # sha256 of canonical JSON
sql_hash(compiled) -> str   # sha256 of canonical JSON {sql, params without as_of}
```

The SQL is a single `SELECT DISTINCT u."<id>" AS canonical_user_id FROM "<schema>"."<users>" AS u WHERE … ORDER BY 1` with `EXISTS`/`NOT EXISTS`/scalar sub-selects; identifiers come from the snapshot and are always double-quoted; every literal is a `%(pN)s` parameter; relative windows use the bound `%(as_of)s` (execution time, UTC). Business-context `exclusion` entries are applied to every cohort.

### LLM provider (FR-10)

```python
class LlmSettings(BaseSettings):   # COHORTSPLIT_LLM_*
    base_url: str | None; model: str | None; api_key: SecretStr | None
    timeout_seconds: float = 60; max_attempts: int = 3; json_mode: bool = True
class ChatClient(Protocol):  def complete(self, messages: Sequence[ChatMessage]) -> str
class OpenAICompatibleClient(ChatClient)   # POST {base_url}/chat/completions, Bearer key if set
LlmUnavailableError(base_url_without_credentials), LlmNotConfiguredError, LlmInvalidOutputError(attempts)
LlmResult = Interpretation(kind="interpretation", spec: CohortSpecV1)
          | Clarification(kind="clarification", question: str)
          | Rejection(kind="rejection", reason: "mutation"|"analytics"|"unknown_term"|"unsupported_construct", message: str)
interpret_request(client, prompt) -> LlmResult   # bounded retries with the validation error fed back
build_prompt(snapshot, nl_request) -> list[ChatMessage]
```

### HTTP API (all `require_permission("cohort.create")`, CSRF, `{"error": {...}}` errors)

| Method & path | Body | Result |
|---|---|---|
| `POST /api/cohorts/interpret` | `{nl_request}` | `{kind: "interpretation", interpretation_id, readable, spec, spec_hash, semantic_version, expires_at}` · `{kind: "clarification", question, unknown_terms, unknown_values, semantic_version}` · `{kind: "rejection", reason, message, semantic_version}`. 409 `canonical_user_id_missing` / `schema_inventory_unavailable`; 503 `llm_not_configured` / `llm_unavailable`; 502 `llm_invalid_output`. Nothing is executed. |
| `POST /api/cohorts/preview` | `{interpretation_id, spec_hash, semantic_version, export_columns: []}` | `{prepared_cohort_id, total_count, empty, split_export_enabled, disabled_reason, sample: {columns, rows}, sql, sql_hash, spec_hash, semantic_version, null_counts, row_cap, expires_at}`. 404 `interpretation_not_found`; 409 `spec_hash_mismatch` / `semantic_version_changed`; 403 `export_column_denied`; 422 `export_column_unsupported` / `unknown_value` / `row_cap_exceeded`; 504 `query_timeout`; 503 `warehouse_unavailable`; 500 `sql_validation_failed` (logged as a defect). |

### Prepared cohort (for `feature/experiment-split`, ruling C4)

```python
@dataclass(frozen=True) class PreparedCohort:
    id: str; created_by: int; created_at; expires_at; nl_request: str
    spec: Mapping; spec_hash: str; sql: str; params: Mapping[str, JsonScalar]; sql_hash: str
    semantic_version: str; dialect: str; as_of: datetime; row_count: int
class PreparedCohortStore:
    def get(self, db, prepared_id: str, *, user_id: int, now) -> PreparedCohort | None   # owner only, not expired
def execute_membership(executor, prepared, *, allowed_tables) -> tuple[str, ...]          # re-validates, same as_of
```

## Data-model changes (`0005_cohort_compiler`, `down_revision = "0004_semantic_context"`)

| Table / change | Columns |
|---|---|
| `cohort_interpretations` | `id uuid PK`, `created_by → users`, `created_at`, `expires_at`, `nl_request text`, `spec jsonb`, `spec_hash char(64)`, `semantic_version char(64)` |
| `prepared_cohorts` | `id uuid PK`, `interpretation_id → cohort_interpretations`, `created_by → users`, `created_at`, `expires_at`, `nl_request`, `spec jsonb`, `spec_hash`, `sql text`, `params jsonb`, `sql_hash`, `semantic_version`, `dialect`, `as_of timestamptz`, `row_count int` — no result rows, no PII columns |
| `example_use_cases` (data) | rows with `spec_version = 'draft-0'` rewritten to v1 (`referenced_columns` recomputed); downgrade converts back |

## Implementation steps

1. Commit this plan.
2. **Slice A – spec v1 + upgrade** (RED → GREEN): models, draft-0 upgrade, crawler/semantic switch to v1.
3. **Slice B – resolver, readable, compiler, validator** (RED → GREEN, pure).
4. **Slice C – LLM provider, prompt, interpreter, guards** (RED → GREEN, fake LLM + in-process HTTP stub).
5. **Slice D – persistence, service, API, audit, migration** (RED → GREEN).
6. **Slice E – integration**: AC-1..AC-5 reference tests on the demo warehouse, timeout/row-cap compiler path, migration up/down.
7. **Slice F – frontend** New cohort tab (RED → GREEN).
8. Docs (README, rulings), completion gate, container smoke test, review, fixes test-first, PR.

## Tests

### AC → test map

| AC | Test(s) |
|---|---|
| AC-1 | `tests/integration/test_cohort_reference.py::test_ac1_two_delivered_orders_in_last_90_days` (readable text + ID set = reference SQL) |
| AC-2 | `…::test_ac2_country_abandoned_cart_never_paid` (demo), `…::test_ac2_guest_carts_never_match` (scratch schema with guest carts); unit `tests/unit/compiler/test_sql.py::test_related_exists_correlates_on_user_key` |
| AC-3 | `…::test_ac3_category_but_not_product` |
| AC-4 | `…::test_ac4_active_registered_in_year_spent_over_500` (business-context metric `spent`) |
| AC-5 | `…::test_ac5_product_or_discount` |
| AC-6 | `tests/cohort/test_interpret_api.py::test_ac6_best_customers_asks_and_executes_nothing`, `::test_ac6_guard_turns_undefined_term_into_clarification` |
| AC-7 | `…::test_ac7_purchased_recently_asks_for_both_terms` |
| AC-8 | `…::test_ac8_delete_request_rejected_without_llm_or_warehouse`, `::test_ac8_misbehaving_llm_still_rejected`; `tests/unit/cohort/test_guards.py` |
| AC-9 | `…::test_ac9_revenue_question_rejected_as_analytics` |
| AC-10 | `tests/unit/compiler/test_sql.py::test_same_spec_and_version_compile_byte_identical`, `::test_compilation_independent_of_snapshot_order`; `tests/cohort/test_preview_api.py::test_preview_sql_identical_across_runs` |
| AC-11 | `tests/unit/compiler/test_validator.py` (each DML/DDL/DCL keyword, multiple statements, CTE with DML, `SELECT … INTO`, `COPY`, `pg_sleep`/`dblink`/`lo_*`, comments, locking clauses, non-allowlisted tables/schemas) |
| AC-28 | `tests/unit/llm/test_prompt.py::test_prompt_never_contains_credentials_rows_or_pending_use_cases`, `tests/cohort/test_interpret_api.py::test_prompt_and_logs_free_of_credentials` (captured prompt + caplog), `tests/unit/llm/test_client.py::test_api_key_never_logged_or_in_errors` |
| AC-29 | `tests/cohort/test_cohort_endpoint_security.py` (401, 403 per endpoint, CSRF), route inventory in `tests/auth/test_endpoint_security.py` |
| AC-30 | `tests/unit/llm/test_client.py::test_switching_cloud_and_local_is_configuration_only` (two configs against an in-process OpenAI-compatible stub) |
| FR-6 limits | `tests/integration/test_cohort_execution.py::test_compiler_path_timeout_error`, `::test_compiler_path_row_cap_error`; `tests/cohort/test_preview_api.py::test_row_cap_refusal_states_cap` |
| FR-7 | `tests/cohort/test_preview_api.py` (count, samples limited to ID + permitted columns, NULL counts, SQL, empty cohort disables split, version change → 409, export-column policy) |
| BR-6 | `tests/unit/cohort/test_guards.py::test_sequence_wording_is_not_supported_yet`, spec model forbids unknown node types |
| Draft-0 | `tests/unit/cohort_spec/test_upgrade.py`, `tests/integration/test_migration_cohort_compiler.py` |
| Audit | `tests/cohort/test_cohort_audit.py` |
| Frontend | `frontend/src/cohort/NewCohortTab.test.tsx` (each result type, confirm → preview, errors, hidden without permission) |

## Edge cases

Empty cohort (count 0, split/export disabled with reason) · row cap exceeded (refused with cap) · timeout (error with timeout value) · unknown product/category name (422 `unknown_value` after a bounded lookup at preview) · unknown enumerable value (clarification with "did you mean") · mixed valid + sequence clause (whole request rejected) · guest carts (`user_id IS NULL`) never match · stale business entry (absent from snapshot → treated as undefined) · semantic version changed between interpret and preview (409) · expired or foreign interpretation (404) · canonical user id missing (409, no LLM call) · LLM down/timeout (503 naming the base URL without credentials) · invalid LLM output (bounded retry, then 502; raw output logged without secrets).

## Security implications

- The LLM produces only a typed spec; executed SQL is compiled deterministically and validated by an AST allowlist independent of the LLM; execution uses only the read-only role through `ReadOnlyExecutor`.
- Prompt content comes from the snapshot only (BR-7); warehouse credentials, result rows and pending use cases are never included (tested by capturing the prompt).
- Literals are bound parameters; identifiers come from the crawl inventory and are quoted.
- Export columns in preview go through `PolicyService.effective_export_columns`; denied requests are refused and audited.
- Every endpoint behind `require_permission("cohort.create")`; interpretations are bound to their creator.
- Prepared cohorts store definitions and hashes, never result rows.

## Acceptance criteria

AC-1 … AC-11, AC-28, AC-29 (new endpoints) and AC-30 pass as mapped; the container smoke test (create-admin, login, crawl, canonical user id + business context, interpret the AC-2 request with a stub LLM, confirm, preview) succeeds.

## Definition of done

Tests-only `test:` commits (with RED summaries) precede the implementation commits that make them pass; `make lint`, `make typecheck`, `make test`, `make up` + `make test-integration`, alembic upgrade head → downgrade base → upgrade head pass with fresh output; smoke test done; independent review Critical/Important findings fixed test-first; README + rulings updated; PR open with CI green.
