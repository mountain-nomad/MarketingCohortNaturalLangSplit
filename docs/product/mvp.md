# Feature: CohortSplit MVP

Status: draft — produced via `grill-me` on 2026-10-09. Authentication/RBAC behavior is specified separately in `docs/product/authentication.md` (in progress).

## Problem

Today, launching a targeted push-notification campaign requires a data analyst who:

1. knows the business rules (what counts as a "purchase", an "active user", etc.);
2. writes correct SQL against the warehouse to extract the target cohort;
3. manually splits the cohort into test and control groups so the campaign can be measured as an A/B experiment.

The marketer waits on the analyst for every campaign. The MVP removes the analyst from the per-campaign loop: the marketer describes the audience in plain English, the system extracts matching users from the warehouse, deterministically splits them into **test** (receives the campaign) and **control** (does not), and returns one export per group.

The analyst's knowledge does not disappear — it moves into a reviewed **business-context** layer, maintained by authorized users (e.g. analysts) in an admin page and used for every request.

Both halves are essential: NL → correct cohort **and** cohort → reproducible test/control split.

## Users

| Role | Who | MVP responsibilities |
|---|---|---|
| **Admin** (protected system role) | Person who deploys the instance | Configures datasource and LLM (server config); manages users and **custom roles with any combination of permissions**; has every permission |
| **Analyst** (example custom role) | Knows the data and business rules | Runs the crawler; in the **admin page**: reviews generated docs, edits business context, confirms example use cases |
| **Marketer** (example custom role, primary user) | CRM / push marketer, no SQL | In the **user dashboard**: describes cohort, confirms interpretation, configures split, downloads exports |

"Analyst" and "Marketer" are illustrative roles, not hardcoded: business roles are data created by the admin from an atomic permission catalog (see `access-control` skill). Every action in this spec is gated by a backend-enforced permission.

MVP deployment model: **one instance, per-user login, admin-managed dynamic roles.** Authentication, RBAC, the admin dashboard, and the user dashboard are part of the MVP but delivered as a separate feature/PR (`feature/authentication-rbac`) with their own spec.

## User Flow

### Setup (admin, then analyst)

```text
clone repo
  -> configure datasource (PostgreSQL, read-only DB user) and LLM provider in config/env
  -> start app (docker compose)
  -> bootstrap the first admin via CLI; admin creates roles, users, and per-role
     export-column grants (e.g. users.phone)
  -> run metadata crawler
  -> crawler writes generated semantic docs: schema, relationships, sample values,
     row counts, and data-driven example use cases (NL -> spec pairs), all "pending review"
  -> in the admin page, a user with semantic-context permissions reviews generated docs, writes business context
     (e.g. "purchase = orders.status IN ('paid','shipped','delivered')"),
     and confirms / edits / rejects each example use case
```

### Campaign (marketer, per campaign)

```text
1. Type request:  "Users from Kazakhstan who abandoned a cart and never paid for an order"
2. System returns ONE of:
     a. Interpretation — human-readable conditions derived from the structured spec
     b. Clarification question — e.g. "Does 'purchased' mean paid or delivered?"
     c. Rejection — with reason (mutation request / analytics question / unknown term /
        unsupported construct)
3. Marketer confirms interpretation (or rephrases and resubmits)
4. System compiles SQL deterministically, validates it, executes read-only
5. Preview: total cohort size, sample of rows, generated SQL (read-only view),
   count of NULLs in each selected export column
6. Marketer configures split:
     - experiment key (required, e.g. push_black_friday_2026)
     - test / control allocation in percent (default 50/50)
     - export columns: canonical user ID (always) + optional columns the user's roles
       are granted for export
7. System assigns users deterministically and shows test and control counts
8. Marketer downloads one ZIP: <key>_test.csv, <key>_control.csv, manifest.json
   The run is recorded in the History tab
9. (Outside the product) marketer sends the push campaign to the test group only;
   control receives nothing; results are compared later
```

## Functional Requirements

### FR-1 Datasource
- PostgreSQL only in MVP, accessed through a warehouse-adapter boundary so other DWHs are an adapter addition, not a domain rewrite.
- Single datasource, configured via config/env (no datasource-management UI).
- Credentials never sent to the LLM, never logged.

### FR-2 Metadata crawler
- Discovers schemas, tables/views, columns, types, PKs, FKs/relationships, DB comments, estimated row counts.
- Collects bounded sample/distinct values for low-cardinality columns (e.g. `orders.status`, `addresses.country_code`), subject to the sampling policy (see Permissions and Security).
- Produces two kinds of example output:
  - **(a) Example use cases** — NL request → cohort-spec pairs.
  - **(b) Data-profile examples** — sample values and row counts per table.
- **Example use cases are data-driven.** The crawler generates use cases that the discovered data can actually answer. When a template or previously defined use case is not supported by the current data (e.g. "users who liked product X" on a warehouse with no likes data), the crawler rewrites it into the closest use case the data does support (e.g. "users who bought product X") or drops it, and records why.
- Every generated or rewritten use case is **pending review** until the analyst confirms it in the admin page. Pending use cases are never used by the system.
- Machine-generated content and human-authored content are distinguishable; re-running the crawler must not overwrite human edits or confirmed use cases. If a confirmed use case is no longer supported after a schema change, it is flagged for re-review rather than silently changed.

### FR-3 Business context & admin page
- Users holding the relevant permission edit business context in the **admin page**: terms/synonyms, metric definitions, status semantics, default time windows, exclusions, canonical user identifier.
- The admin page also shows generated semantic docs and the example-use-case review queue (confirm / edit / reject).
- Human-reviewed context is authoritative over LLM inference.
- Every change produces a semantic-context version identifier recorded with each execution and export.

### FR-4 NL → structured cohort specification
- LLM converts the request into a typed, schema-validated cohort spec. **The LLM never produces the SQL that runs.**
- Spec supports: entity (user), attribute filters, existence/non-existence of related records, nested AND/OR/NOT, time windows (relative and absolute), count/sum thresholds over related records.
- Only confirmed example use cases are used as few-shot context.
- Invalid LLM output (schema violation) → bounded retry, then explicit error.
- Unresolvable terms → clarification or error naming the unknown term. Never invent tables, columns, events, or definitions.

### FR-5 Interpretation preview & confirmation
- Before executing anything against the warehouse, show the interpretation as readable conditions, e.g.
  `country = KZ  AND  has cart with status = abandoned  AND  NOT has order with status = paid`.
- Execution requires explicit marketer confirmation.

### FR-6 Deterministic SQL compilation & validation
- Same spec + same semantic version + same dialect → byte-identical SQL.
- AST-based validation: single read-only `SELECT`; reject DML/DDL/DCL; allowlisted schemas/tables only.
- Statement timeout (default 30s) and result-row cap (default 1,000,000) enforced; both configurable.
- Output is one row per canonical user (deduplicated).

### FR-7 Cohort preview
- Total count, sample rows (bounded), generated SQL (read-only, visible), NULL counts for selected export columns.

### FR-8 Deterministic test/control split
- Exactly **two groups: `test` and `control`.** Test receives the campaign; control is the holdout that receives nothing.
- Required experiment key (marketer-supplied string).
- Configurable allocation as integer percentages summing to 100, each group ≥ 1%. Default 50/50.
- Assignment = explicitly specified stable hash of `experiment_key + canonical_user_id` → bucket → group. No runtime randomness, no language-runtime `hash()`.
- Same key + same user → same group, across runs, restarts, and changed cohort definitions (with the same allocation).
- Groups are disjoint and their union equals the cohort.
- The same machinery supports A/A checks (both groups treated identically).

### FR-9 Export
- One ZIP per split containing `<key>_test.csv`, `<key>_control.csv`, and `manifest.json`.
- Each CSV always contains the canonical user ID column; additional columns **only if one of the user's roles holds an export grant for that column**, checked at download time.
- Manifest: original NL request, structured spec, compiled SQL, semantic-context version, experiment key, allocation, per-group counts, timestamp.
- One row per user per file.

### FR-9a Run history
- Every executed split is recorded as a **run**: original NL request, structured spec, compiled SQL, semantic-context version, experiment key, allocation, per-group counts, creator, timestamp, and the **membership snapshot** (canonical user ID + assigned group for every cohort member).
- The snapshot stores **only canonical user IDs and groups** — no PII.
- **History tab** in the user dashboard lists runs. A user sees their own runs; users with `cohort.read_all` (admin always) see everyone's.
- **Re-download** rebuilds the ZIP from the stored snapshot: membership and groups are exactly the original; extra columns (e.g. `phone`) are fetched again from the warehouse at download time, subject to the user's **current** export grants. Users no longer present in the warehouse appear with empty extra columns and are counted in the manifest.
- Runs are not re-executed (no refresh); the stored snapshot is the record of the experiment population.

### FR-10 LLM provider
- One provider interface speaking the OpenAI-compatible API: configurable base URL, model name, optional API key.
- Covers cloud OpenAI-compatible providers and locally hosted models (e.g. Qwen via Ollama / vLLM / LM Studio).
- Provider choice is configuration only. MCP not required.

### FR-11 Demo environment
- `docker compose` brings up PostgreSQL seeded with the `ecommerce` schema from `harryho/db-samples`, fetched at build time pinned to commit `9bd103fc8b12b1453c8d02571e413fc7fa65ecdb` (the upstream repo has no license file, so its SQL is not vendored into this repo).
- No supplementary demo data: use cases the demo data cannot support (likes, referral campaigns) are handled by the crawler's rewrite behavior (FR-2).
- Plus the application and its own metadata store.

## Business Rules

- BR-1: The cohort entity is the user; canonical identifier is configured by the admin (demo: `users.user_id`).
- BR-2: Business definitions come from reviewed business context. If a term is used but undefined and not unambiguously resolvable from schema, the system asks rather than guesses.
- BR-3: Requests that are not cohort definitions (analytics questions like "revenue last month") are rejected with an explanation.
- BR-4: Requests implying writes ("delete inactive users") are rejected, and SQL validation rejects writes independently.
- BR-5: Group assignment depends only on experiment key, user ID, and allocation — not on cohort size, row order, or run time.
- BR-6: Ordered sequences ("added to cart, *then* didn't buy within 24h") are out of MVP; such requests return an explicit "not supported yet" message.
- BR-7: Nothing generated by the system (docs, use cases) influences cohort interpretation until a human has confirmed it, except raw schema metadata.

## Data Requirements

- Source of truth: the configured PostgreSQL warehouse (demo: `ecommerce`).
- Demo semantics needing business context: `orders.status` ∈ {pending, paid, processing, shipped, delivered, cancelled}; `carts.status` ∈ {active, converted, abandoned}; guest carts have `user_id IS NULL` and never belong to a cohort; soft-deleted users (`deleted_at IS NOT NULL`) and `is_active` semantics must be defined in business context.
- Time semantics: relative windows are evaluated against execution time in UTC; columns are `timestamptz`. Absolute date ranges are interpreted in UTC.
- NULL semantics: a user with NULL in an optional export column (e.g. `phone`) remains in the cohort; the preview reports how many such NULLs exist.
- Duplicates: cohort output is deduplicated on canonical user ID.
- Reproducibility: rerunning the same request later may yield a different cohort (data changes); group assignment for any user present in both runs is identical.
- Scale: demo data is tiny (~91 users, ~830 orders). No performance targets in MVP beyond enforced timeouts and row caps.

## Permissions and Security

Authentication and role-based authorization are part of the MVP (separate feature). This spec assumes every action below is gated by them.

Still enforced in MVP (non-negotiable, fail closed):
- **Read-only DB user** at the database level, in addition to application-level SQL validation.
- AST-based SQL validation (single `SELECT`, no DML/DDL/DCL, allowlisted schemas/tables).
- Statement timeout and row cap.
- Warehouse credentials never reach the LLM or logs.
- **Per-role export-column grants**; the canonical user ID is the only column exportable by default. Requests for non-granted columns are refused server-side, including on re-download.
- **Authentication and RBAC:** every endpoint requires an authenticated user and the permission for that action, enforced on the backend (see `docs/product/authentication.md`).
- **Sampling policy:** sample values collected only for low-cardinality columns; never for columns granted for export or columns on a configurable denylist (default includes names like `email`, `phone`, `password*`, `*token*`, `address*`). Sample values may be sent to a remote LLM; sampling can be disabled entirely by config.
- LLM only sees schema metadata, semantic docs, confirmed use cases, and policy-permitted sample values — never cohort result rows.
- Structured application logs per execution (request, spec hash, SQL hash, semantic version, row count, experiment key) without PII. Operational logging, separate from the audit log.
- **Audit log** (MVP): cohort runs, exports and re-downloads, user/role/permission/grant changes, semantic-context changes — see `docs/product/authentication.md`.

Deferred to post-MVP (architecture must allow them): row-level security, table-level grants, PII classification.

## Edge Cases

- Empty cohort → preview shows 0; split and export are disabled with an explanation.
- Cohort so small that test or control receives 0 users → warning; export requires explicit confirmation.
- Allocation not summing to 100, or either group < 1% → validation error.
- Missing / blank experiment key → validation error. Reused key with a different cohort → allowed (BR-5).
- Request references a product/category name that does not exist → clarification ("No product named X; did you mean …?") or explicit unknown-value error.
- Request mixes a valid cohort with an unsupported construct (sequence) → reject whole request with explanation; never silently drop a clause.
- Cohort exceeds row cap → block execution/export with a message stating the cap.
- Crawler re-run after schema change → generated sections update, human sections and confirmed use cases preserved; confirmed use cases that are no longer supported are flagged for re-review; semantic version changes.
- Guest carts (`user_id IS NULL`) never produce cohort rows.

## Failure Behavior

| Failure | Behavior |
|---|---|
| LLM unreachable / timeout | Actionable error ("LLM provider unavailable at <base URL>"); nothing executed |
| LLM returns invalid spec | Bounded retry, then error; raw LLM output logged for debugging (no secrets) |
| Ambiguous request | Clarification question returned; nothing executed |
| Unknown term / no business definition | Error naming the term; suggests adding it to business context |
| DWH unreachable | Actionable error; no partial export |
| SQL validation failure | Execution refused; error shown; logged as a defect (compiler should never produce invalid SQL) |
| Query timeout | Error with timeout value; suggest narrowing the request |
| Row cap exceeded | Execution/export refused with cap value |
| Crawler fails mid-run | Previous docs and confirmed use cases remain intact; error reported |

## Non-Goals (MVP)

- Cohort refresh (re-executing a past run against current data); history keeps the original snapshot only.
- Warehouses other than PostgreSQL.
- More than two groups (multi-arm tests).
- Sample-size / MDE / power calculator and experiment result analysis.
- Ordered event sequences.
- Visual editing of the structured spec (marketer rephrases instead).
- Direct integration with push providers; delivery is by CSV download.
- General BI / analytics question answering.
- Supplementary demo datasets.
- MCP.

## Acceptance Criteria

Demo dataset = `ecommerce` from `harryho/db-samples`. Product/category names in examples are placeholders replaced by real demo values in tests.

### Must-work requests
- **AC-1** Given reviewed business context, when the marketer submits "Users who placed at least 2 delivered orders in the last 90 days", then the interpretation shows `count(orders where status = delivered and ordered_at within 90 days) ≥ 2`, and the executed cohort equals a hand-written reference SQL result.
- **AC-2** "Users from Kazakhstan who abandoned a cart and never paid for an order" → cohort equals reference result; guest carts excluded.
- **AC-3** "Users who bought anything from category Electronics but not product X" → cohort equals reference result (includes NOT across the same relation).
- **AC-4** "Active users who registered in 2024 and spent more than $500 in total" → "spent" resolved via business context; cohort equals reference result.
- **AC-5** "Users who bought product X or used a discount" → OR across different relations; cohort equals reference result.

### Must-clarify requests
- **AC-6** Given no definition of "best customer", when the marketer submits "Our best customers", then the system returns a clarification question and executes nothing.
- **AC-7** Given "purchase" is undefined in business context, when the marketer submits "Users who purchased recently", then the system asks what "purchased" and "recently" mean and executes nothing.

### Must-reject requests
- **AC-8** When the marketer submits "Delete inactive users", then the request is rejected and no statement reaches the warehouse.
- **AC-9** When the marketer submits "What was revenue last month?", then it is rejected as not a cohort request.

### Compiler & SQL safety
- **AC-10** Given the same spec and semantic version, compiling twice yields byte-identical SQL.
- **AC-11** Given a SQL string containing INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE or multiple statements, validation rejects it.
- **AC-12** Given the configured DB user, when any write statement is attempted directly, the database refuses it (read-only enforced at DB level).
- **AC-13** Given a query exceeding the statement timeout, execution is aborted with a timeout error.

### Test/control split
- **AC-14** Given experiment key K and a cohort, running the split twice (including across an app restart) yields identical assignments.
- **AC-15** Given key K, the same allocation, and two different cohorts sharing user U, U is in the same group in both.
- **AC-16** Test and control are disjoint and their union equals the cohort.
- **AC-17** Given a synthetic population of 100,000 IDs and a 50/50 split, each group's share is within ±1 percentage point; same for a 90/10 split.
- **AC-18** Given an allocation not summing to 100, a group below 1%, or an empty key, the split is refused with a validation error.

### Export & data policy
- **AC-19** Given none of the user's roles is granted export of `users.phone`, when an export with `phone` is requested (including via direct API call), the server refuses it.
- **AC-20** Given a role of the user is granted export of `users.phone`, the export contains `user_id, phone`, one row per user, and the preview reports the NULL-phone count.
- **AC-21** The export ZIP contains `<key>_test.csv`, `<key>_control.csv`, and a manifest with NL request, spec, SQL, semantic version, experiment key, allocation, and per-group counts.

### Run history
- **AC-21a** Given a completed run, when its creator re-downloads it from History, then test/control membership is identical to the original export.
- **AC-21b** Given the user lost the `users.phone` export grant after the original export, when they re-download with `phone`, then the server refuses it.
- **AC-21c** The stored run snapshot contains no columns other than canonical user ID and group.
- **AC-21d** Given user A without `cohort.read_all`, A cannot list or download user B's runs (including via direct API).

### Crawler, use cases & admin page
- **AC-22** Running the crawler on the demo DB produces docs listing all `ecommerce` tables, columns, PK/FK relationships, row counts, sample values for `orders.status` and `carts.status`, and data-driven example use cases marked pending review.
- **AC-23** Given a use-case template "Users who liked product X" and a warehouse with no likes data, the crawler outputs a rewritten use case the data supports (or drops it) with a recorded reason, marked pending review.
- **AC-24** Pending use cases are not included in LLM prompts; after the analyst confirms one in the admin page, it is.
- **AC-25** Given the analyst edited business context in the admin page, re-running the crawler does not overwrite it, and the semantic version changes only when content changes.
- **AC-26** Given a confirmed use case referencing a column that a schema change removed, the crawler re-run flags it for re-review.
- **AC-27** No sample values are collected for denylisted columns (e.g. `users.email`, `users.phone`, `users.password_hash`).
- **AC-28** Warehouse credentials never appear in LLM prompts or application logs.
- **AC-29** Every admin and user-dashboard API endpoint refuses unauthenticated requests and requests lacking the required permission (detailed criteria in `docs/product/authentication.md`).

### LLM provider
- **AC-30** Switching between a cloud OpenAI-compatible endpoint and a local Qwen endpoint requires configuration changes only.

### Setup
- **AC-31** On a clean machine with Docker, following the README, a user can bring up the demo DB + app and complete AC-2 end-to-end.

## Resolved Decisions

| Topic | Decision |
|---|---|
| MVP scope | NL→spec→SQL, crawler + docs, business context, CSV export, basic test/control split |
| Primary value | Both cohort extraction and split |
| Deployment | Per-user login; admin manages dynamic roles (separate feature `feature/authentication-rbac`) |
| SQL generation | LLM → structured spec → deterministic compiler; LLM never writes executed SQL |
| Interpretation | Shown to marketer and confirmed before execution |
| Export columns | User ID by default; extra columns (e.g. phone) only via per-role export grants |
| History | Runs stored as ID+group snapshot; re-download re-fetches extra columns under current permissions; own runs, or all with `cohort.read_all` |
| Audit log | MVP |
| RLS / table grants | Post-MVP; architecture must allow them |
| UI | Web UI for marketers; admin page for business logic |
| LLM | One OpenAI-compatible interface; cloud or local (Qwen) |
| Demo data | `ecommerce` from `harryho/db-samples`, pinned, fetched at build; no supplementary data |
| Example use cases | Data-driven, crawler rewrites unsupported ones, analyst confirms |
| Business context editing | Admin page, permission-gated |
| Project name | CohortSplit |
| Groups | Exactly test + control; configurable %, default 50/50 |
| Export packaging | ZIP: test CSV, control CSV, manifest |
| Limits | 30s timeout, 1,000,000-row cap, configurable |
| SQL visibility | Visible read-only in preview |
| Remote LLM sampling | Allowed under denylist; can be disabled |

## MVP Feature Breakdown

Every major feature gets its own branch, product spec (when needed), plan in `docs/plans/`, and PR. Proposed order:

| # | Branch | Scope | Depends on |
|---|---|---|---|
| 0 | `feature/mvp-spec` | This document | — |
| 1 | `feature/project-skeleton` | Repo layout, backend/frontend scaffolding, docker compose, demo Postgres (FR-11), CI, lint/type/test tooling | 0 |
| 2 | `feature/authentication-rbac` | Login, first-admin CLI, users, dynamic roles/permissions, column export grants, audit log, admin and user dashboard shells | 1 |
| 3 | `feature/warehouse-crawler` | PostgreSQL adapter, read-only execution, metadata crawler, generated docs, sampling policy (FR-1, FR-2) | 1 |
| 4 | `feature/semantic-context` | Business context editing, versioning, use-case review queue in admin page (FR-3) | 2, 3 |
| 5 | `feature/cohort-compiler` | LLM provider, NL → spec, deterministic compiler, SQL validation, interpretation + preview (FR-4–FR-7, FR-10) | 3, 4 |
| 6 | `feature/experiment-split` | Test/control assignment, export ZIP + manifest, export-grant enforcement, run history tab (FR-8, FR-9, FR-9a) | 2, 5 |

## Open Questions

None for this document. Authentication/RBAC questions are tracked in `docs/product/authentication.md`.
