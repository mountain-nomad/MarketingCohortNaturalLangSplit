# 0001 — Warehouse crawler rulings

Status: accepted (feature/warehouse-crawler) · Date: 2026-10-09

Decisions taken while implementing FR-1/FR-2 where the spec left room. Each picks the fail-closed or most conservative option. Later branches may revisit them explicitly.

## R1. Draft cohort spec `draft-0`

The real cohort specification belongs to `feature/cohort-compiler`. Example use cases still need a structured spec, so they are stored as a versioned JSON document validated by `cohortsplit.cohort_spec.draft.DraftCohortSpec`:

- every document carries `"spec_version": "draft-0"`, and `example_use_cases.spec_version` repeats it;
- nodes: `attribute`, `attribute_time_window`, `related` (FK `path` of qualified joins, `filters`, optional `time_window` and `aggregate` count/sum), `all_of`, `any_of`, `not`;
- all references are qualified (`schema.table`, `schema.table.column`); values are literals; there is no SQL anywhere;
- `referenced_columns(spec)` is stored per use case (`referenced_columns` column) and drives re-review flagging.

**Migration duty (compiler branch):** introduce the real spec with a new `spec_version`, migrate or re-generate rows with `spec_version = 'draft-0'`, and keep `referenced_columns` populated so AC-26 flagging keeps working.

## R2. Export-grant hook

Columns granted for export must never be sampled, but grants arrive with `feature/authentication-rbac`. The crawler takes an `ExportGrantProvider` (`export_granted_columns() -> frozenset[str]`, entries `schema.table.column` or `table.column`) via `run_crawl(..., export_grants=...)`. The default is `NoExportGrants` (empty). The auth branch must pass its grant-backed provider from the CLI/service that triggers crawls.

## R3. Sampling policy

- Only categorical types (string, boolean, enum) are sampled; numbers, dates, JSON, etc. never are.
- The default denylist (`email`, `phone`, `password*`, `*token*`, `address*`, plus `*email*`, `*phone*`, `*password*`, `*secret*`, `*_hash`, `first_name`, `last_name`, `*line1`, `*line2`, `*postal_code*`, `ship_name`) always applies. `COHORTSPLIT_CRAWLER_SAMPLE_DENYLIST` **extends** it and cannot remove entries.
- "Low-cardinality" means at most `COHORTSPLIT_CRAWLER_SAMPLE_MAX_DISTINCT` (default 50) distinct non-null values **and** no more distinct values than half the rows (near-unique values in small tables look like identifiers, e.g. names). Values that fail the second rule are read but discarded, never stored.
- `COHORTSPLIT_CRAWLER_SAMPLING_ENABLED=false` disables every value read (samples and key examples).
- Each column's profile records why it was or was not sampled, for reviewers.

## R4. Key examples for use-case values

Templates such as "bought product X" need a real value for X, and product names are usually high-cardinality. The crawler reads the smallest primary-key value (one value) of single-key tables, subject to the same policy (denylist, export grants, sampling switch). It never does this for the user entity table, so no user IDs are stored. If no permitted value exists, the template is dropped with a reason.

## R5. Template vocabulary, not customer schema

Templates recognise roles by generic names (`orders`/`purchases`/`transactions`, `products`, `categories`, `carts`, like/favorite/wishlist tables, `status` columns, …) and by FK paths. The user entity table is detected by name (`users`, `customers`, …) or configured with `COHORTSPLIT_CRAWLER_USER_TABLE=schema.table`. CHECK-constraint value lists count as schema metadata and can fill status values when no samples exist.

## R6. Re-run semantics

- Generated use cases are keyed by template key (one use case per template per crawl).
- Pending generated use cases are replaced. Confirmed, rejected or flagged generated use cases are kept, and their key is not regenerated, so a rejected suggestion does not come back.
- Human use cases and human docs are never modified, except that a **confirmed** use case of any origin whose referenced table/column is gone becomes `needs_rereview` with a `review_note`. Its spec is not changed.
- Flagging only considers references inside the crawled schemas when `COHORTSPLIT_CRAWLER_SCHEMAS` limits the crawl.
- Pending human use cases are not flagged: they are already awaiting review.
- Editing a generated use case (semantic-context branch) should set `origin = 'human'` so that later crawls leave it alone.

## R7. Atomicity and versioning

Warehouse reads complete before appdb changes. The swap is one transaction under a transaction-scoped advisory lock, and a failed crawl is recorded as `failed` with a sanitized error. `crawl_runs.content_hash` is a SHA-256 over canonical generated content with no ids or timestamps. The semantic-context branch derives the semantic version from it plus human content.

## R8. No HTTP endpoints; audit seam

Endpoints must be auth-gated (AC-29), so this branch exposes only `run_crawl` and `cohortsplit crawl`. `crawl_runs.triggered_by` is `"cli"` for now. `CrawlAuditHook` (`crawl_started`/`crawl_succeeded`/`crawl_failed`) is the seam for the audit log, and `NullCrawlAuditHook` is the default.

## R9. Migration chaining

`0003_warehouse_metadata` uses `down_revision = "0001_baseline"`. Whichever of the auth and crawler branches merges second re-chains its migration. `test_migrations.py` compares against the script head rather than a hardcoded revision.
