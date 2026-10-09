# Semantic Context Implementation Plan

Branch: `feature/semantic-context` · MVP Feature Breakdown row 4 · Implements **FR-3** (business context & admin page), the use-case review queue of **FR-2**, the semantic-context parts of **BR-2** and **BR-7**, the "Setup" user flow, the "Semantic context" admin tab of `docs/product/authentication.md` FR-A6, and the semantic-context / crawler events of FR-A5. Acceptance criteria: **AC-24**, **AC-25**, **AC-26** (through the HTTP crawl endpoint) and **AC-29** for every endpoint added here.

Depends on `feature/warehouse-crawler` (rulings 0001: R1 draft-0 spec, R2 export-grant hook, R6 re-run rules) and `feature/authentication-rbac` (`require_permission`, `AuditService`, `RoleExportGrants`, `AuditCrawlHook`).

## Problem

The crawler stores generated docs and pending example use cases, but nobody can review them, there is no place for the analyst's business rules ("purchase = orders.status IN ('paid','shipped','delivered')"), no semantic version to stamp on cohort runs and exports, and no way to run the crawler except a shell. The cohort compiler (next branch) needs one deterministic, BR-7-safe source of LLM context.

## Desired behavior

- A user with `semantic_context.read` opens the **Semantic context** admin tab and sees: generated docs (tables, columns, PK/FK relationships, row counts, policy-permitted sample values), business-context entries, the example-use-case review queue with statuses, crawl runs and the current semantic version.
- A user with `semantic_context.edit` creates, edits and deletes typed **business-context entries**. Every schema reference is checked against the latest crawl; unknown tables/columns are refused.
- A user with `use_case.review` confirms, edits (the use case becomes human-authored and confirmed) or rejects use cases, including `needs_rereview` ones (highlighted).
- A user with `crawler.run` triggers a crawl from the page; a second concurrent crawl is refused with 409. Re-crawls never overwrite business context or reviewed use cases.
- Every content change produces a new **semantic version** (deterministic content hash) recorded in a history with actor and cause; a save that changes nothing keeps the version.
- The compiler branch gets `SemanticContextProvider` whose output contains only confirmed use cases, human business context, raw schema metadata and policy-permitted samples.

## Scope

Backend `backend/src/cohortsplit/semantic/`:

| File | Responsibility |
|---|---|
| `context.py` | Business-context entry models (typed definitions, filters, normalization). Pure. |
| `inventory.py` | `SchemaInventory` from generated `table_schema` docs; reference validation. Pure. |
| `version.py` | Canonical content hashing → `SemanticVersion`. Pure. |
| `snapshot.py` | `SemanticContextSnapshot` and `assemble_snapshot(...)` (BR-7 filtering, sample re-check). Pure. |
| `models.py` | ORM: `BusinessContextEntry`, `SemanticVersionRecord`. |
| `repository.py` | Reads/writes on a SQLAlchemy `Session` (entries, use cases, docs, version history). |
| `service.py` | `SemanticContextService`: edits/reviews + audit + version record in one transaction under the semantic lock. |
| `provider.py` | `SemanticContextProvider`, `current_semantic_version(db)`. |
| `crawl.py` | `CrawlCoordinator`: advisory-lock guard, `run_crawl` with `RoleExportGrants` + `AuditCrawlHook`, version record. |
| `api.py` | `/api/semantic/*` and `/api/crawler/runs` routers. |
| `wiring.py` | `install_semantic(app, settings, engine)`; `CrawlEnvironment` dependency. |

Plus: migration `0004_semantic_context`; additive edits to `crawler/tables.py` (review columns), `crawler/store.py` (template keys held by human rows are not regenerated), `crawler/cli.py` (record semantic version after a CLI crawl), `audit/actions.py`, `app.py`, `alembic/env.py` untouched. Frontend `frontend/src/semantic/` replaces the placeholder tab. Docs: this plan, `docs/decisions/0002-semantic-context-rulings.md`, README section.

## Non-goals

- The real cohort spec, LLM prompts, the compiler (next branch). Use cases keep `draft-0`.
- Creating brand-new human use cases from scratch (edit covers human authoring; follow-up).
- Free-form human docs editing (`semantic_docs` origin `human`): business context replaces it for MVP.
- Asynchronous/background crawls and crawl cancellation.
- Validating status values against sampled values, type compatibility of filter literals, FK join paths (compiler resolves paths from raw FK metadata).

## Proposed interfaces

### Business-context entry (ruling S1)

```python
# cohortsplit.semantic.context
Scalar = str | int | float | bool
class Filter(BaseModel):            # frozen, extra=forbid
    column: QualifiedColumn         # "schema.table.column"
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "in", "not_in", "is_null", "is_not_null"]
    value: Scalar | tuple[Scalar, ...] | None = None   # None iff is_null/is_not_null; non-empty tuple iff in/not_in

class TermDefinition:              kind="term";              table: QualifiedTable | None; column: QualifiedColumn | None   # exactly one
class MetricDefinition:            kind="metric";            table; filters: tuple[Filter, ...] = (); value_column: QC | None; time_column: QC | None
class StatusSemanticsDefinition:   kind="status_semantics";  column; meanings: dict[str, str]  (>= 1)
class TimeWindowDefinition:        kind="time_window";       last_days: int (1..3650); column: QC | None
class ExclusionDefinition:         kind="exclusion";         table; filters: tuple[Filter, ...] (>= 1)
class CanonicalUserIdDefinition:   kind="canonical_user_id"; column
Definition = Annotated[Term | Metric | StatusSemantics | TimeWindow | Exclusion | CanonicalUserId, Field(discriminator="kind")]
# metric/exclusion: every filter/value/time column belongs to `table`.

class BusinessContextIn(BaseModel):     # API body and service input
    key: str            # ^[a-z][a-z0-9_]{0,63}$, immutable
    synonyms: tuple[str, ...] = ()      # normalized: trimmed, whitespace-collapsed, casefolded, unique, sorted
    description: str = ""               # free text, <= 4000
    definition: Definition

def definition_references(d: Definition) -> tuple[frozenset[str], frozenset[str]]  # (tables, columns)
```

Examples: `purchase` = `{"kind":"metric","table":"public.orders","filters":[{"column":"public.orders.status","operator":"in","value":["paid","shipped","delivered"]}],"time_column":"public.orders.created_at"}`; `canonical_user` = `{"kind":"canonical_user_id","column":"public.users.user_id"}`; `recently` = `{"kind":"time_window","last_days":30}`; `soft_deleted_users` = `{"kind":"exclusion","table":"public.users","filters":[{"column":"public.users.deleted_at","operator":"is_not_null"}]}`.

Rules: keys and synonyms are one namespace (a term resolves to at most one entry, 409 `term_conflict`); at most one `canonical_user_id` entry (409 `canonical_user_id_exists`); the canonical column must be the single-column primary key of its table.

### Reference validation

```python
# cohortsplit.semantic.inventory
@dataclass(frozen=True) class TableShape: columns: frozenset[str]; primary_key: tuple[str, ...]
@dataclass(frozen=True) class SchemaInventory:
    tables: Mapping[str, TableShape]                       # "schema.table" -> shape
    @classmethod def from_table_docs(cls, contents: Iterable[Mapping]) -> "SchemaInventory"
    def has_table(t) / has_column(c) -> bool
@dataclass(frozen=True) class ReferenceProblem: reference: str; problem: str
def check_definition(d: Definition, inv: SchemaInventory) -> tuple[ReferenceProblem, ...]
def check_spec(spec: DraftCohortSpec, inv: SchemaInventory) -> tuple[ReferenceProblem, ...]
```

No crawl yet → 409 `schema_inventory_unavailable` for every write that needs references (fail closed).

### Semantic version (ruling S3)

```python
# cohortsplit.semantic.version
VERSION_FORMAT = 1
@dataclass(frozen=True) class SemanticVersion:
    version: str                       # sha256 hex
    business_context: str; confirmed_use_cases: str; generated_docs: str   # component hashes
def compute_semantic_version(entries: Iterable[EntryContent], confirmed: Iterable[UseCaseContent],
                             generated_docs: Iterable[DocContent]) -> SemanticVersion
```

Canonical JSON (sorted keys, compact, UTF-8) with no ids, timestamps or authors:
- business context: `{key, kind, synonyms, description, definition}` sorted by key;
- confirmed use cases: `{nl_request, spec, spec_version}` sorted by their canonical JSON (origin/key/ids excluded);
- generated docs: `{doc_key, kind, content}` of every `origin='generated'` doc sorted by `(doc_key, kind)`.
`version = sha256({"format":1,"business_context":h1,"confirmed_use_cases":h2,"generated_docs":h3})`.

### Provider (for `feature/cohort-compiler`)

```python
# cohortsplit.semantic.provider
def current_semantic_version(db: Session) -> SemanticVersion

class SemanticContextProvider:
    def __init__(self, session_factory: sessionmaker[Session], *, crawler_settings: CrawlerSettings,
                 export_grants: ExportGrantProvider) -> None
    def snapshot(self) -> SemanticContextSnapshot          # everything below, read in ONE transaction
    def confirmed_use_cases_for_prompt(self) -> tuple[PromptUseCase, ...]
    def current_semantic_version(self) -> str
    def canonical_user_id(self) -> str | None              # "schema.table.column" or None (compiler must refuse)

# cohortsplit.semantic.snapshot
@dataclass(frozen=True) class PromptUseCase: id: int; nl_request: str; spec: Mapping; spec_version: str
@dataclass(frozen=True) class PromptBusinessContext: key; kind; synonyms; description; definition: Mapping
@dataclass(frozen=True) class PromptColumn: name; data_type; nullable; comment; allowed_values; sample_values: tuple[str, ...]
@dataclass(frozen=True) class PromptTable: qualified_name; kind; comment; columns; primary_key; foreign_keys; estimated_row_count
@dataclass(frozen=True) class SemanticContextSnapshot:
    semantic_version: str
    canonical_user_id: str | None
    business_context: tuple[PromptBusinessContext, ...]     # only entries whose references resolve
    stale_business_context: tuple[str, ...]                 # keys left out (missing references)
    confirmed_use_cases: tuple[PromptUseCase, ...]          # status == confirmed only
    tables: tuple[PromptTable, ...]                         # raw schema metadata + samples
```

BR-7: pending, rejected and `needs_rereview` use cases, human/generated free-text docs and generation notes are never in the snapshot. Samples are re-checked against the **current** sampling policy (sampling switch, denylist, export grants): a sample stored before a grant was added is not exposed (closes crawler R2 gap for prompts and the docs view).

### HTTP API

Errors use the existing `{"error": {"code", "message", ...}}` shape. All endpoints require a session (401 `not_authenticated`), CSRF on unsafe methods, and the permission below (403 `permission_denied`, audited `access.denied`).

| Method & path | Permission | Notes |
|---|---|---|
| `GET /api/semantic/docs` | `semantic_context.read` | `{tables:[...], latest_run}`: columns, PK, FKs, row counts, policy-permitted samples |
| `GET /api/semantic/business-context` | `semantic_context.read` | items with `missing_references` |
| `POST /api/semantic/business-context` | `semantic_context.edit` | 201; 409 `business_context_key_taken` / `term_conflict` / `canonical_user_id_exists` / `schema_inventory_unavailable`; 422 `invalid_reference` (`problems`) / `validation_error` |
| `PUT /api/semantic/business-context/{key}` | `semantic_context.edit` | full replace (key immutable); 404 |
| `DELETE /api/semantic/business-context/{key}` | `semantic_context.edit` | 204; 404 |
| `GET /api/semantic/use-cases` | `semantic_context.read` | `?status=`; items incl. origin, status, notes, reviewer |
| `POST /api/semantic/use-cases/{id}/confirm` | `use_case.review` | from pending_review / needs_rereview / rejected; references must resolve (422 `invalid_reference`); 409 `invalid_transition` |
| `POST /api/semantic/use-cases/{id}/reject` | `use_case.review` | from pending_review / needs_rereview / confirmed; optional `note` |
| `PUT /api/semantic/use-cases/{id}` | `use_case.review` | `{nl_request, spec}` → `origin=human`, `status=confirmed`; spec validated as draft-0 + references |
| `GET /api/semantic/version` | `semantic_context.read` | current version + components |
| `GET /api/semantic/versions` | `semantic_context.read` | history, newest first, `limit`/`offset` |
| `POST /api/crawler/runs` | `crawler.run` | synchronous crawl → 201 run; 409 `crawl_in_progress`; 502 `crawl_failed` (`run_id`); 503 `warehouse_not_configured` |
| `GET /api/crawler/runs` | `semantic_context.read` | newest first, `limit` |

## Data-model changes (Alembic `0004_semantic_context`, `down_revision = "0002_auth"`)

| Table / change | Columns |
|---|---|
| `business_context_entries` (ORM) | `id bigint identity PK`, `key text UNIQUE`, `kind text CHECK IN (...)`, `synonyms jsonb`, `description text`, `definition jsonb`, `created_at`, `updated_at` (timestamptz), `created_by`, `updated_by` → `users.id` |
| `semantic_versions` (ORM, append-only) | `id`, `version char(64)`, `components jsonb`, `cause text`, `target text NULL`, `actor_type text CHECK IN ('user','cli')`, `actor_user_id → users NULL`, `created_at`; index on `created_at` |
| `example_use_cases` | `+ reviewed_by → users NULL`, `+ reviewed_at timestamptz NULL` |

Downgrade drops both tables and both columns.

## Implementation steps

1. Commit this plan.
2. **Slice A – pure domain** (RED → GREEN): context models, inventory/reference checks, version hashing, snapshot assembly (`tests/unit/semantic/`).
3. **Slice B – persistence, service, provider, API** (RED → GREEN): migration, ORM, repository, service with audit + version history, provider, `/api/semantic/*`, endpoint security matrix, migration tests.
4. **Slice C – crawl API** (RED → GREEN): coordinator with advisory lock, `/api/crawler/runs`, human-content preservation and AC-26 via HTTP, CLI version recording, crawler store fix for edited template keys.
5. **Slice D – frontend** (RED → GREEN): Semantic context tab with sub-views and permission-gated controls.
6. Docs (README, decision record), completion gate, smoke test in containers, independent review, fixes test-first, PR.

## Tests

Backend API tests reuse the auth fixtures (`tests/semantic/conftest.py`): data tests run on the migrated PostgreSQL test database (crawler tables are PostgreSQL-only, marked `integration`); the 401/403 matrix runs on SQLite **and** PostgreSQL. Crawl tests use the read-only warehouse role against a scratch schema.

### AC → test map

| AC | Test(s) |
|---|---|
| AC-24 | `tests/semantic/test_provider.py::test_pending_use_cases_excluded_then_included_after_confirm` (through the HTTP confirm endpoint), `::test_rejected_and_needs_rereview_never_in_prompt`, `::test_snapshot_contains_only_br7_sources`; unit `tests/unit/semantic/test_snapshot.py::test_only_confirmed_use_cases_reach_the_snapshot`, `::test_samples_rechecked_against_current_policy` |
| AC-25 | `tests/semantic/test_crawl_api.py::test_recrawl_via_http_preserves_business_context_and_confirmed_use_cases`; `tests/semantic/test_semantic_version.py::test_version_changes_once_per_content_change_and_is_stable_on_reread`, `::test_saving_identical_content_keeps_version`, `::test_recrawl_of_unchanged_warehouse_keeps_version`; unit `tests/unit/semantic/test_version.py` (determinism, order independence, sensitivity per component, ids/timestamps/authors excluded) |
| AC-26 | `tests/semantic/test_crawl_api.py::test_http_recrawl_flags_confirmed_use_case_whose_column_was_removed` |
| AC-29 | `tests/semantic/test_semantic_endpoint_security.py::test_every_semantic_endpoint_refuses_unauthenticated`, `::test_every_semantic_endpoint_refuses_missing_permission`, `::test_semantic_mutations_require_csrf`, `::test_admin_passes_every_semantic_permission_check`; `tests/auth/test_endpoint_security.py::test_route_inventory_is_fully_classified` (extended) |
| FR-3 validation | `tests/semantic/test_business_context_api.py` (create/list/update/delete, unknown table/column refused, no crawl → 409, term conflict, singleton canonical id, canonical must be PK, key immutable); unit `tests/unit/semantic/test_context_models.py`, `test_inventory.py` |
| FR-2 review queue | `tests/semantic/test_use_case_review_api.py` (list with statuses, confirm/reject/edit transitions, edit → human + confirmed, re-review of `needs_rereview`, invalid transitions, edited spec validated, 404) |
| FR-A5 audit | `tests/semantic/test_semantic_audit.py` (one event per edit/decision with actor, target, before/after and versions; denied attempts audited; edits/decisions fail closed with 503 `audit_unavailable` and no change when the audit store is down; crawl start/run events) |
| Concurrency | `tests/semantic/test_crawl_api.py::test_second_concurrent_crawl_refused_with_409` |
| Versions history | `tests/semantic/test_semantic_version.py::test_history_records_actor_and_cause`, `::test_cli_crawl_records_version` |
| Frontend | `frontend/src/semantic/SemanticTab.test.tsx` (controls hidden per permission, docs view, editor create + error display, review confirm/reject/edit, `needs_rereview` highlighted, crawl button + 409) |
| Migration | `tests/integration/test_migration_semantic_context.py` (single head, upgrade/downgrade, constraints); ORM parity in `tests/auth/test_schema_postgres.py` |

## Edge cases

- No crawl yet: docs empty, business-context writes needing references → 409 `schema_inventory_unavailable`, confirm → 409 as well.
- Column removed after an entry was saved: entry listed with `missing_references`, left out of the prompt snapshot (`stale_business_context`), never auto-deleted.
- Edited generated use case keeps its template key; later crawls do not regenerate that key (human row occupies it).
- Crawl replaces pending use cases: a stale id in the UI → 404 on confirm (never confirms different content).
- Concurrent edits/reviews/crawl swaps are serialized by one transaction-scoped advisory lock, so version history is linear.
- Saving identical content → no new version row; audit event still recorded (it was an action).
- Crawl failure → 502, previous content intact, run recorded `failed`, audited.

## Security implications

- Every endpoint behind `require_permission`; security matrix + route inventory test prevent unguarded additions.
- BR-7 enforced in one place (`assemble_snapshot`), tested at unit and HTTP level.
- Samples re-filtered with the current policy before reaching the docs view or the provider.
- Sensitive audit (`record`, same transaction) for business-context edits and review decisions: audit failure → 503, no change. API crawl writes a sensitive `crawler.start` event before touching the warehouse.
- No SQL accepted anywhere: filters are typed literals on qualified, inventory-checked columns.
- Crawl uses only the read-only warehouse role and the existing `RoleExportGrants` provider.

## Rulings

S1 business-context shape; S2 canonical user id resolution (no fallback); S3 version formula and history; S4 edit = human + confirmed; S5 confirm requires resolvable references; S6 stale entries excluded from prompt; S7 samples re-checked against today's policy; S8 synchronous, exclusive crawl API; S9 reads need `semantic_context.read`; S10 audit. Recorded in `docs/decisions/0002-semantic-context-rulings.md`.

## Acceptance criteria

AC-24, AC-25, AC-26 (via HTTP) and AC-29 for every new endpoint pass as mapped; the container smoke test (create-admin, login, crawl via API, add business context, confirm a use case, version changes once and is stable on re-read) succeeds.

## Definition of done

Tests-only `test:` commits (with RED summaries) precede the implementation commits; `make lint`, `make typecheck`, `make test`, `make up` + `make test-integration`, alembic upgrade head → downgrade base → upgrade head all pass with fresh output; smoke test done; independent review findings (Critical/Important) fixed test-first; README + decision record updated; PR open with CI green.
