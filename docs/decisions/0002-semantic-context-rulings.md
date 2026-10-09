# 0002 — Semantic context rulings

Status: accepted (feature/semantic-context) · Date: 2026-10-10

Decisions taken while implementing FR-3, the FR-2 review queue and the semantic version, where the spec left room. Each picks the fail-closed or most conservative option.

## S1. Business-context shape

Business context is a set of **typed, structured entries** (`cohortsplit.semantic.context`), not free-form documents:

```json
{
  "key": "purchase",
  "synonyms": ["bought", "purchased"],
  "description": "An order the customer actually paid for.",
  "definition": {
    "kind": "metric",
    "table": "public.orders",
    "filters": [{"column": "public.orders.status", "operator": "in", "value": ["paid", "shipped", "delivered"]}],
    "value_column": "public.orders.grand_total",
    "time_column": "public.orders.ordered_at"
  }
}
```

- `definition.kind` ∈ `term` (names one table or column), `metric` (rows of one table matching filters, optional value/time column), `status_semantics` (meaning per value of a column), `time_window` (`last_days`, optional column), `exclusion` (rows of one table that remove users from every cohort), `canonical_user_id` (one column).
- Filters are `{column, operator, value}` with operators `= != > >= < <= in not_in is_null is_not_null` and literal values. There is no SQL anywhere. Metric/exclusion columns must belong to the entry's table; joins are resolved by the compiler from raw FK metadata.
- `key` is an immutable slug (`^[a-z][a-z0-9_]{0,63}$`). Synonyms are normalized (NFKC, trimmed, whitespace collapsed, case-folded, unique, sorted).
- **Keys and synonyms share one namespace**: a term resolves to at most one entry (409 `term_conflict`), so the compiler's lookup is deterministic.
- Every referenced table/column must exist in the generated `table_schema` docs of the latest crawl (422 `invalid_reference`); before any crawl, writes that reference the schema are refused (409 `schema_inventory_unavailable`).
- Human business context is authoritative: crawls never write to `business_context_entries`.

## S2. Canonical user identifier

BR-1 says the admin configures it. It is the single `canonical_user_id` entry (409 `canonical_user_id_exists` for a second one), and its column must be the **single-column primary key** of its table (dedup guarantee). There is **no fallback** to the crawler's detected user table: `SemanticContextProvider.canonical_user_id()` returns `None` until the entry exists (or while its column is missing), and the compiler must refuse to run a cohort in that case with an actionable message ("define the canonical user identifier in Business context").

## S3. Semantic version

`version = sha256(canonical JSON {format: 1, business_context, confirmed_use_cases, generated_docs})`, each component the SHA-256 of canonical JSON (sorted keys, compact, UTF-8) of:

- business context `{key, kind, synonyms, description, definition}` sorted by key;
- **confirmed** use cases `{nl_request, spec, spec_version}` sorted by canonical JSON (ids, origin, reviewer and notes excluded);
- every generated doc `{doc_key, kind, content}` sorted by `(doc_key, kind)`.

It is computed from stored content, never cached, so it changes only when that content changes (identical saves, unchanged re-crawls and pending suggestions do not change it). The generated-docs component hashes the stored docs rather than the latest run's `content_hash`, so scoped crawls (`COHORTSPLIT_CRAWLER_SCHEMAS`) are covered too. `semantic_versions` keeps a history row (version, components, cause, target, actor, time) whenever a change produces a version different from the last recorded one; edits, review decisions, API crawls and CLI crawls all record. Edits, decisions and crawl swaps are serialized by one transaction-scoped advisory lock.

## S4. Editing a use case confirms it

`PUT /api/semantic/use-cases/{id}` stores the reviewer's text and spec, sets `origin = human` and `status = confirmed` (the reviewer holds `use_case.review` and authored it). The template key is kept on the row, and the crawler no longer regenerates a template key held by a human row (crawler R6 follow-up). Edited specs are validated as `draft-0` and against the latest crawl.

## S5. Confirmation requires resolvable references

Confirming (from `pending_review`, `needs_rereview` or `rejected`) re-checks the spec against the latest crawl; a spec that references a missing table/column is refused (422 `invalid_reference`) and must be edited instead. Repeating a decision is 409 `invalid_transition`. Reject is allowed from `pending_review`, `needs_rereview` and `confirmed`, with an optional note.

## S6. Stale business context is left out of LLM context

An entry whose references disappeared after a re-crawl is never auto-deleted or rewritten. It is listed with `missing_references` in the admin page and **excluded** from the provider snapshot (`stale_business_context` names it) so the compiler asks rather than compiling against a missing column.

## S7. Samples are re-checked against today's policy

Generated docs and the provider expose a stored sample only if the **current** sampling policy still permits it (sampling switch, denylist, export grants). A column export-granted after the last crawl is not exposed (closes the crawler R2 window for prompts and the admin page).

## S8. Crawl API is synchronous and exclusive

`POST /api/crawler/runs` crawls synchronously (MVP warehouses are small) and returns the run (201), 502 `crawl_failed` with the run id, or 409 `crawl_in_progress`. Exclusivity is a session-level PostgreSQL advisory lock held for the whole crawl, shared with `cohortsplit crawl`. Before touching the warehouse a sensitive `crawler.start` audit event is written (503 `audit_unavailable` if it cannot be); a refused concurrent request is audited as `crawler.start` / `denied`. The run itself is recorded by the existing `AuditCrawlHook` as `crawler.run` with the user as actor.

## S9. Read permissions

Listing use cases, crawl runs, versions and generated docs needs `semantic_context.read` (the tab's permission); acting needs `use_case.review`, `semantic_context.edit` or `crawler.run`. A user with only `use_case.review` cannot see the queue, matching FR-A6 where the tab itself requires `semantic_context.read`.

## S10. Audit

`semantic_context.create|update|delete` (before/after content, versions before/after) and `use_case.confirm|reject|edit` (from/to status, origin, before/after for edits) are **sensitive**: written in the same transaction as the change, so an audit failure refuses the change (503). Permission denials are audited by `require_permission` (`access.denied`).
