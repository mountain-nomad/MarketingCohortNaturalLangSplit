# Warehouse Crawler Implementation Plan

Branch: `feature/warehouse-crawler` · MVP Feature Breakdown row 3 · Implements FR-1 (Datasource), FR-2 (Metadata crawler), the sampling policy (Permissions and Security), the crawler rows of Edge Cases / Failure Behavior, and acceptance criteria AC-12, AC-13, AC-22, AC-23, AC-26, AC-27, AC-28 plus the crawler-side half of AC-25. Respects BR-7.

## Problem

The skeleton can reach the demo warehouse only from tests. There is no adapter boundary, no read-only executor with timeouts and row caps, and nothing that discovers what the warehouse contains. Later features (semantic context, compiler) need:

- a warehouse-agnostic adapter so PostgreSQL specifics stay in one place;
- a safe executor (read-only transaction, statement timeout, row cap, typed actionable errors, no credential leakage);
- generated, reviewable documentation of the warehouse (schema + data profile) and data-driven example use cases, kept distinguishable from human-authored content and never clobbering it on re-run.

## Desired behavior

- `cohortsplit crawl` (CLI, inside the app container or on the host) connects to the configured warehouse as the read-only role, discovers schemas, tables/views, columns, types, PKs, FKs, comments, estimated row counts and CHECK-constraint value lists, collects bounded distinct samples only for policy-permitted low-cardinality columns, generates example use cases (NL request → draft cohort spec) from deterministic templates, and stores everything in appdb in one transactional swap. It prints a summary and exits 0; on failure it prints an actionable error, exits non-zero, and leaves previous content intact.
- Every generated use case is `pending_review`. Unsupported templates are rewritten to the closest supported use case (e.g. "liked product X" → "bought product X") or dropped, with the reason recorded.
- A re-run replaces generated content only; human-authored docs/use cases and confirmed/rejected use cases are untouched; confirmed use cases that reference tables/columns that no longer exist are flagged `needs_rereview`.
- Each crawl stores a content hash of the generated content that changes only when the content changes.

## Scope

- `backend/src/cohortsplit/warehouse/` — adapter protocol, PostgreSQL adapter, read-only executor, typed errors, factory.
- `backend/src/cohortsplit/crawler/` — settings, sampling policy, metadata collection, use-case templates/generator, persistence store (SQLAlchemy Core), service, audit seam, CLI registration.
- `backend/src/cohortsplit/cohort_spec/draft.py` — minimal versioned draft spec model (`spec_version: "draft-0"`).
- `backend/alembic/versions/0003_warehouse_metadata.py` (`down_revision = "0001_baseline"`; the orchestrator re-chains after the auth branch merges).
- Small additive edits: `config.py` (timeout, row cap, connect timeout), `cli.py` (subcommand registration), `Makefile` (test-only admin DSN for scratch schemas), `.env.example`/README docs, `docs/decisions/`.

## Non-goals

- HTTP endpoints (every endpoint must be auth-gated per AC-29; endpoints arrive in `feature/semantic-context`).
- Review queue UI, editing business context, semantic version derivation (semantic-context branch; this branch only provides the per-crawl content hash).
- SQL AST validation, the real cohort spec, NL interpretation, LLM provider (compiler branch).
- Export grants and audit log (auth branch). This branch exposes seams only.
- Warehouses other than PostgreSQL.

## Proposed interfaces

```python
# cohortsplit.warehouse
class WarehouseError(Exception)                      # base, message is actionable, never contains DSN
class WarehouseNotConfiguredError(WarehouseError)
class WarehouseUnavailableError(WarehouseError)      # "Warehouse is unreachable at host:port/db ..."
class QueryTimeoutError(WarehouseError)              # .timeout_seconds
class RowCapExceededError(WarehouseError)            # .row_cap
class ReadOnlyViolationError(WarehouseError)         # DB refused a write
class WarehouseQueryError(WarehouseError)            # other DB errors (sanitized)

@dataclass(frozen=True) QueryResult(columns: tuple[str, ...], rows: tuple[tuple[object, ...], ...])
@dataclass(frozen=True) TableRef(schema, name, kind: "table"|"view"|"materialized_view", comment)
@dataclass(frozen=True) ColumnInfo(name, data_type, nullable, ordinal, comment, allowed_values)
@dataclass(frozen=True) ForeignKey(name, columns, referred_schema, referred_table, referred_columns)
@dataclass(frozen=True) TableMetadata(ref, columns, primary_key, foreign_keys, estimated_row_count)

class WarehouseAdapter(Protocol):
    dialect: str
    def describe_location(self) -> str                 # "host:port/db", no credentials
    def test_connection(self) -> None
    def list_schemas(self) -> list[str]
    def list_tables(self, schema: str) -> list[TableRef]
    def describe_table(self, table: TableRef) -> TableMetadata
    def get_distinct_values(self, table: TableRef, column: str, max_distinct: int) -> list[str] | None
    def get_key_examples(self, table: TableRef, column: str, limit: int) -> list[str]
    def execute_readonly(self, sql: str, params: Mapping[str, object] | None = None) -> QueryResult

class ReadOnlyExecutor:                               # cohortsplit.warehouse.executor
    def __init__(self, dsn: SecretStr, *, statement_timeout_seconds: float = 30,
                 row_cap: int = 1_000_000, connect_timeout_seconds: int = 5)
    def execute(self, sql, params=None, *, row_cap: int | None = None) -> QueryResult

class PostgresWarehouseAdapter(WarehouseAdapter)      # cohortsplit.warehouse.postgres
def create_warehouse_adapter(settings: Settings) -> PostgresWarehouseAdapter   # cohortsplit.warehouse.factory

# cohortsplit.cohort_spec.draft
SPEC_VERSION = "draft-0"
class DraftCohortSpec(BaseModel): spec_version: Literal["draft-0"]; entity: EntityRef; where: Condition
  Condition = AllOf | AnyOf | Not | AttributeCondition | RelatedCondition
def referenced_tables(spec) -> set[str]; def referenced_columns(spec) -> set[str]

# cohortsplit.crawler
class CrawlerSettings(BaseSettings)   # prefix COHORTSPLIT_CRAWLER_
class SamplingPolicy: decide(table, column, data_type) -> SamplingDecision(allowed, reason)
class ExportGrantProvider(Protocol): def export_granted_columns(self) -> frozenset[str]
class NoExportGrants(ExportGrantProvider)            # default until the auth branch merges
def collect_catalog(adapter, policy, schemas) -> WarehouseCatalog
def generate_use_cases(catalog, user_table=None) -> UseCaseGeneration(use_cases, dropped)
class CrawlStore: (appdb) start_run, finish_run_failed, swap_generated_content, list_docs,
    list_use_cases, list_confirmed_use_cases, get_run, add_human_use_case, upsert_human_doc, set_use_case_status
class CrawlAuditHook(Protocol): crawl_started / crawl_succeeded / crawl_failed   # seam for the auth branch
class NullCrawlAuditHook
def run_crawl(adapter, store, *, settings, export_grants=NoExportGrants(), audit=NullCrawlAuditHook(), triggered_by=None) -> CrawlReport
cohortsplit crawl [--json]                            # registered from cohortsplit/crawler/cli.py
```

## Data-model changes (appdb, migration `0003_warehouse_metadata`)

- `crawl_runs` — `id bigint identity PK`, `status text CHECK IN ('running','succeeded','failed')`, `started_at timestamptz`, `finished_at timestamptz NULL`, `triggered_by text NULL` (actor seam for auth), `content_hash char(64) NULL`, `summary jsonb` (counts, dropped templates with reasons, flagged use cases), `error text NULL` (sanitized).
- `semantic_docs` — `id`, `doc_key text` (`table:<schema>.<table>`), `kind text CHECK IN ('table_schema','data_profile','note')`, `origin text CHECK IN ('generated','human')`, `content jsonb`, `crawl_run_id FK NULL ON DELETE SET NULL`, `created_at`, `updated_at`; `UNIQUE (doc_key, kind, origin)`.
- `example_use_cases` — `id`, `use_case_key text`, `origin`, `status text CHECK IN ('pending_review','confirmed','rejected','needs_rereview')`, `nl_request text`, `spec jsonb`, `spec_version text`, `template_key text NULL`, `rewritten_from text NULL`, `generation_note text NULL` (why rewritten), `review_note text NULL` (why flagged), `referenced_columns jsonb`, `crawl_run_id FK NULL`, timestamps; partial unique index on `use_case_key WHERE origin = 'generated'`.

## Implementation steps

1. This plan (commit 1).
2. Slice A — warehouse adapter + executor: tests (unit: config defaults, error sanitization, unreachable; integration: AC-12, AC-13, row cap, introspection) + stubs → RED commit; implement → GREEN commit.
3. Slice B — sampling policy + metadata collection: tests (unit with fake adapter; integration AC-22 metadata, AC-27) + stubs → RED; implement → GREEN.
4. Slice C — draft spec + use-case generation: tests (unit AC-23, determinism, kept/rewritten/dropped) + stubs → RED; implement → GREEN.
5. Slice D — persistence, service, CLI, migration: tests (integration AC-25 crawler half, AC-26, failure atomicity, content hash, AC-28 logs; unit CLI) + stubs → RED; implement → GREEN.
6. Docs (README, decision record), completion gate, review, fixes test-first, PR.

## Tests (AC → test map)

| AC | Test(s) |
|---|---|
| AC-12 | `tests/integration/test_warehouse_executor.py::test_executor_write_refused_by_database` (parametrized INSERT/UPDATE/DELETE/CREATE/DROP/TRUNCATE/ALTER), `::test_executor_connects_as_read_only_role`; existing `test_warehouse_readonly.py` |
| AC-13 | `tests/integration/test_warehouse_executor.py::test_statement_timeout_aborts_query_with_timeout_error`, `::test_timeout_error_states_value`; unit `tests/unit/warehouse/test_executor_settings.py::test_defaults_30s_and_1m_rows` |
| (FR-6 limits) | `test_warehouse_executor.py::test_row_cap_exceeded_raises_with_cap`, `::test_rows_at_cap_are_returned` |
| AC-22 | `tests/integration/test_crawler_demo.py::test_demo_crawl_lists_all_tables_and_columns`, `::test_demo_crawl_pk_fk_relationships`, `::test_demo_crawl_row_counts`, `::test_demo_crawl_samples_order_and_cart_status`, `::test_demo_crawl_generates_pending_use_cases` |
| AC-23 | `tests/unit/crawler/test_use_cases.py::test_liked_product_rewritten_to_bought_product_without_likes_data`, `::test_liked_product_kept_when_likes_data_exists`, `::test_template_dropped_with_reason`, `::test_all_generated_use_cases_pending_review`; `tests/integration/test_crawler_demo.py::test_demo_liked_product_rewritten_with_real_value` |
| AC-25 (crawler half) | `tests/integration/test_crawl_persistence.py::test_rerun_preserves_human_docs_and_use_cases`, `::test_rerun_preserves_confirmed_and_rejected_generated_use_cases`, `::test_generated_and_human_content_distinguishable`, `::test_content_hash_stable_when_unchanged_and_changes_with_schema` |
| AC-26 | `tests/integration/test_crawl_persistence.py::test_confirmed_use_case_flagged_when_column_removed`, `::test_confirmed_use_case_flagged_when_table_removed` |
| AC-27 | `tests/integration/test_crawler_demo.py::test_no_samples_for_denylisted_user_columns` (`users.email`, `users.phone`, `users.password_hash`); unit `tests/unit/crawler/test_sampling_policy.py` (denylist globs, export-granted columns, disabled sampling, adapter never called for denied columns) |
| AC-28 | `tests/unit/warehouse/test_executor_errors.py::test_unreachable_error_has_no_password_or_dsn`, `::test_adapter_repr_hides_dsn`; `tests/integration/test_crawl_service.py::test_crawl_logs_and_stored_content_contain_no_credentials`; `tests/unit/crawler/test_crawl_cli.py::test_crawl_cli_unreachable_warehouse_output_has_no_password` |
| Failure behavior | `tests/integration/test_crawl_persistence.py::test_failed_crawl_mid_run_leaves_previous_content_intact`, `::test_failed_swap_rolls_back_and_marks_run_failed` |
| BR-7 | `tests/integration/test_crawl_persistence.py::test_list_confirmed_use_cases_excludes_pending` |

AC-26 and failure tests use a scratch schema created in the `ecommerce` warehouse with the admin credentials **in tests only** (`COHORTSPLIT_TEST_WAREHOUSE_ADMIN_DSN`, exported by `make test-integration`); the crawler itself always uses `cohortsplit_ro` and is pointed at the scratch schema via `COHORTSPLIT_CRAWLER_SCHEMAS`.

## Edge cases

- Warehouse unreachable / wrong password → `WarehouseUnavailableError` naming `host:port/db` and `COHORTSPLIT_WAREHOUSE_DSN`, never the DSN; crawl run recorded `failed`; previous content intact.
- `COHORTSPLIT_WAREHOUSE_DSN` unset → `WarehouseNotConfiguredError`, CLI exits 2.
- Empty warehouse / no user table → docs for whatever exists; user-centric templates dropped with reason.
- Tables never analyzed (`reltuples = -1`) → exact bounded `count(*)` fallback.
- Column with more distinct values than the cap → no samples (`not low-cardinality`).
- Table/column names needing quoting → identifiers always quoted via `psycopg.sql.Identifier`.
- Sampling disabled → no sample values and no key examples; templates needing a value are dropped with reason; status templates can still use CHECK-constraint values (schema metadata).
- Two crawls concurrently → swap serialized by a transaction-scoped advisory lock.
- Re-run where a generated use case was confirmed/rejected → the same key is not regenerated as pending.

## Security implications

- App code uses only `cohortsplit_ro`; every executor transaction is `READ ONLY` with `SET LOCAL statement_timeout`; the DB itself refuses writes (AC-12).
- DSN held as `SecretStr`; the executor stores it privately, `repr` and error messages show only `host:port/db`; connection errors are re-raised `from None` with a sanitized message (AC-28).
- Sampling policy fails closed: non-text types, denylisted names (built-in defaults cannot be removed, only extended), export-granted columns, the user entity table's key column, and everything when sampling is disabled are never read for values (AC-27).
- Generated content is `pending_review`; nothing in this branch feeds an LLM (BR-7). `list_confirmed_use_cases` is the only read path intended for prompts.
- Admin warehouse credentials appear only in the test environment (Makefile `test-integration`, CI).

## Acceptance criteria

AC-12, AC-13, AC-22, AC-23, AC-26, AC-27, AC-28 and the crawler half of AC-25 pass as mapped above; `cohortsplit crawl` inside the app container against the demo warehouse succeeds and reports 16 tables.

## Definition of done

- Tests-first history: every `test:` commit precedes the commit implementing it and records its RED summary.
- `make lint`, `make typecheck`, `make test`, `make up` + `make test-integration` (incl. `alembic upgrade head` / `downgrade base`) pass with fresh output; `cohortsplit crawl` run in the container.
- Reviewer findings (Critical/Important) fixed test-first.
- README and `docs/decisions/` updated (draft spec format, export-grant hook, sampling defaults); PR open with CI green.
