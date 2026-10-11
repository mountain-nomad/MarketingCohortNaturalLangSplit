# CohortSplit

Self-hosted platform that turns a natural-language audience description into a
warehouse cohort and splits it deterministically into test and control groups.
Product spec: [`docs/product/mvp.md`](docs/product/mvp.md). Contributor rules:
[`AGENTS.md`](AGENTS.md).

## Quick start

Prerequisites: Docker (with compose v2), GNU make, [uv](https://docs.astral.sh/uv/)
0.12+, Node.js 22.

```bash
git clone https://github.com/mountain-nomad/MarketingCohortNaturalLangSplit.git
cd MarketingCohortNaturalLangSplit
cp .env.example .env      # then replace every change-me-* value
make install              # backend (uv) + frontend (npm) dependencies
make up                   # build + start warehouse, appdb, app; waits until healthy
make test                 # backend unit + frontend tests
make test-integration     # backend integration tests against the running stack
```

The app is then at <http://127.0.0.1:8000> (`/api/health`, `/api/ready`).
`make down` stops the stack (data volumes are kept; use `docker compose down -v`
to reset them).

### First admin

There is no self-signup. Create the first admin from the server shell; the password
is prompted without echo (it never goes on the command line or into an env var):

```bash
docker compose exec app cohortsplit create-admin --email admin@example.com
```

Sign in at <http://127.0.0.1:8000/login>. From the **Users** and **Roles** tabs the
admin creates users (each gets a temporary password to change at first sign-in),
custom roles from the permission catalog, and per-role exportable columns.

A locked-out or forgotten admin is recovered from the shell:

```bash
docker compose exec app cohortsplit reset-password --email admin@example.com   # temporary password, clears lockout
docker compose exec app cohortsplit create-admin --email someone@example.com   # existing user: grants Admin, reactivates
```

| Target | What it does |
|---|---|
| `make install` | `uv sync` + `npm ci` |
| `make lint` | ruff (check + format) and ESLint |
| `make typecheck` | mypy (strict) and `tsc` |
| `make test` | backend unit tests + Vitest |
| `make test-integration` | integration tests; fail if the stack is not reachable |
| `make up` / `make down` | start (wait for healthy) / stop docker compose |
| `make migrate` | `alembic upgrade head` against the compose appdb |
| `make crawl` | run the metadata crawler in the app container |

## Services

| Service | Purpose | Host port |
|---|---|---|
| `warehouse` | Demo PostgreSQL with the `ecommerce` sample from [harryho/db-samples](https://github.com/harryho/db-samples), fetched at image build time from pinned commit `9bd103f` and checksum-verified (not vendored: upstream has no license). Includes read-only role `cohortsplit_ro`. | `127.0.0.1:55432` |
| `appdb` | PostgreSQL for application metadata | `127.0.0.1:55433` |
| `app` | FastAPI backend serving the built React frontend; applies migrations on start | `127.0.0.1:8000` |

## Configuration

The backend reads `COHORTSPLIT_*` environment variables (see
`backend/src/cohortsplit/config.py`); `COHORTSPLIT_APPDB_PASSWORD` is required and
startup fails with a message naming any missing or invalid variable. Passwords and
the warehouse DSN are secret values that are never logged or rendered.

Authentication settings (defaults in parentheses):

| Variable | Meaning |
|---|---|
| `COHORTSPLIT_SESSION_IDLE_TIMEOUT_MINUTES` (480) | Session ends after this much inactivity |
| `COHORTSPLIT_SESSION_MAX_LIFETIME_HOURS` (168) | Absolute session lifetime |
| `COHORTSPLIT_LOGIN_MAX_FAILURES` (5) | Failed sign-ins per account within the window before lockout |
| `COHORTSPLIT_LOGIN_FAILURE_WINDOW_MINUTES` (15) | Window for counting failures |
| `COHORTSPLIT_LOGIN_LOCKOUT_MINUTES` (15) | Lockout duration |
| `COHORTSPLIT_COOKIE_SECURE` (auto) | `true` behind HTTPS; unset = `Secure` only when the request is HTTPS |
| `COHORTSPLIT_API_DOCS_ENABLED` (false) | Serve `/api/docs` and `/api/openapi.json` to signed-in users |

Warehouse limits: `COHORTSPLIT_WAREHOUSE_STATEMENT_TIMEOUT_SECONDS` (default 30),
`COHORTSPLIT_WAREHOUSE_ROW_CAP` (default 1,000,000),
`COHORTSPLIT_WAREHOUSE_CONNECT_TIMEOUT_SECONDS` (default 5). Every warehouse
statement runs in its own read-only transaction.

## Metadata crawler

```bash
make crawl                                   # inside the app container
docker compose exec app cohortsplit crawl --json
```

The crawler connects as the read-only role. It documents schemas, tables/views,
columns, types, primary and foreign keys, comments, CHECK value lists and row
counts. It also generates example use cases (NL request → cohort spec v1) from
deterministic templates. Templates the data cannot support are rewritten (e.g.
"liked product X" → "bought product X") or dropped, with the reason recorded.
All output is stored in appdb as generated content with status
**pending review**. A re-run replaces only generated content: human-authored
docs and reviewed use cases are kept, and confirmed use cases whose tables or
columns disappeared are flagged `needs_rereview`. A failed crawl leaves the
previous content intact. Exit codes: 0 ok, 1 crawl failed, 2 configuration
error.

| Variable (`COHORTSPLIT_CRAWLER_*`) | Default | Meaning |
|---|---|---|
| `SAMPLING_ENABLED` | `true` | `false` reads no sample values at all |
| `SAMPLE_MAX_DISTINCT` | `50` | max distinct values for a column to count as low-cardinality |
| `SAMPLE_DENYLIST` | empty | extra comma-separated column globs never sampled; extends built-in PII defaults (`email`, `phone`, `password*`, `*token*`, `address*`, …) |
| `SCHEMAS` | empty (all) | comma-separated schemas to crawl; a missing schema, or one with no readable tables, fails the crawl and changes nothing |
| `USER_TABLE` | detected | user entity table as `schema.table` |

In compose, set `CRAWLER_SAMPLING_ENABLED`, `CRAWLER_SAMPLE_DENYLIST` and
`CRAWLER_SCHEMAS` in `.env`. The rulings behind these defaults are in
[`docs/decisions/0001-warehouse-crawler-rulings.md`](docs/decisions/0001-warehouse-crawler-rulings.md).

## Semantic context (admin page)

The **Semantic context** tab (needs `semantic_context.read`) is where an analyst
turns crawler output into reviewed business meaning:

1. **Crawler** — with `crawler.run`, press *Run crawler* (or `POST /api/crawler/runs`).
   One crawl runs at a time across the API and `cohortsplit crawl`; a second one gets
   409. Re-crawls never overwrite business context or reviewed use cases.
2. **Generated docs** — read-only tables, columns, keys, relationships, row counts and
   the sample values the current sampling policy permits.
3. **Business context** — with `semantic_context.edit`, typed entries: `term`,
   `metric` (e.g. `purchase` = `public.orders.status in [paid, shipped, delivered]`),
   `status_semantics`, `time_window`, `exclusion` and `canonical_user_id`
   (demo: `public.users.user_id`). Every table/column must exist in the latest crawl.
4. **Use cases** — with `use_case.review`, confirm, edit (becomes human-authored and
   confirmed) or reject generated examples; `needs_rereview` items come first. A
   generated example whose value comes from a column the sampling policy no longer
   permits (e.g. export-granted since the crawl) is shown as *Content withheld* and
   can only be rewritten or rejected.

Only confirmed use cases, business context, raw schema metadata and policy-permitted
samples are ever handed to the cohort assistant. Every change to that context (including
a sampling-policy change that hides samples) produces a new **semantic version** (`GET /api/semantic/version`, history at
`/api/semantic/versions`); saving identical content keeps it. Details and rulings:
[`docs/decisions/0002-semantic-context-rulings.md`](docs/decisions/0002-semantic-context-rulings.md).

Try it locally after `make up` and `docker compose exec app cohortsplit create-admin
--email admin@example.com`: sign in at `http://127.0.0.1:${APP_PORT}`, open
*Semantic context → Crawler*, run a crawl, add a `canonical_user_id` entry, and confirm
a use case.

## New cohort (cohort compiler)

The **New cohort** tab (needs `cohort.create`) turns a plain-English request into a
previewed audience:

1. **Interpret** — the request is sent with one semantic snapshot (confirmed use cases,
   business context, schema metadata, policy-permitted samples — never credentials or
   result rows) to the configured LLM, which returns a typed **cohort spec v1**, a
   clarification question, or a rejection (mutation / analytics / unknown term /
   unsupported construct). Deterministic code then checks every table, column, join,
   business term and enumerable value; undefined terms become a question naming them.
   Nothing is executed. Requests that change data, analytics questions and ordered
   sequences ("then …") are rejected before the LLM is called.
2. **Confirm and preview** — confirms the exact spec hash and semantic version (if the
   business context changed meanwhile you get 409 and interpret again). The SQL is
   compiled deterministically (same spec + semantic version → byte-identical SQL), all
   literals are bound parameters, the statement is AST-validated (single read-only
   `SELECT`, allowlisted tables and functions) and executed only through the read-only
   role with the statement timeout and row cap. You see the user count, sample rows
   (canonical id plus columns your roles may export), the read-only SQL and the NULL
   count per selected export column. An empty cohort cannot be split or exported.

A refusal "define the canonical user identifier in Business context" means no
`canonical_user_id` entry exists yet. Interpretations and cohort runs are audited
(`cohort.interpret`, `cohort.run`).

LLM configuration (`COHORTSPLIT_LLM_*`, set `LLM_*` in `.env` for compose). Switching
between a cloud provider and a local model is configuration only:

| Variable | Default | Meaning |
|---|---|---|
| `BASE_URL` | — | OpenAI-compatible base URL, e.g. `https://api.openai.com/v1` or `http://host.docker.internal:11434/v1` (Ollama) |
| `MODEL` | — | model name, e.g. `gpt-4o-mini` or `qwen2.5:7b-instruct` |
| `API_KEY` | empty | bearer key for cloud providers; never logged |
| `TIMEOUT_SECONDS` | `60` | per request |
| `MAX_ATTEMPTS` | `3` | attempts when the output violates the schema |
| `JSON_MODE` | `true` | send `response_format: json_object`; disable for servers without it |

Local Qwen with Ollama: `ollama pull qwen2.5:7b-instruct`, then in `.env`
`LLM_BASE_URL=http://host.docker.internal:11434/v1` and `LLM_MODEL=qwen2.5:7b-instruct`
(no key), `make up`. Cohort settings: `COHORTSPLIT_COHORT_INTERPRETATION_TTL_MINUTES`
(60), `COHORTSPLIT_COHORT_PREPARED_TTL_MINUTES` (1440),
`COHORTSPLIT_COHORT_PREVIEW_SAMPLE_SIZE` (20). Rulings:
[`docs/decisions/0003-cohort-compiler-rulings.md`](docs/decisions/0003-cohort-compiler-rulings.md).

## Split, export and history (experiment split)

After a non-empty preview, the **New cohort** tab offers the split step (needs
`cohort.create`; downloading needs `cohort.export`):

1. **Configure** — experiment key (required; 1–100 ASCII letters, digits, `_`, `-`, `.`;
   it names the files), test share in whole percent (control = 100 − test, each ≥ 1,
   default 50/50), optional **A/A check** (same assignment, both groups treated alike),
   and extra export columns — only columns your roles are granted are offered (the
   canonical user id is always included).
2. **Split** — the cohort is re-executed read-only (same definition, same `as_of`) and
   every user is assigned with a fixed, documented hash: SHA-256 of
   `key || 0x00 || user_id`, first 8 bytes mod 10,000, test if below `test% × 100`. The
   same key and allocation always put a user in the same group — across runs, restarts
   and different cohorts. You see the test/control counts; a group with 0 users is
   flagged and must be confirmed before export. If the business context changed since
   the preview you get 409 and interpret again.
3. **Download** — one ZIP: `<key>_test.csv`, `<key>_control.csv`, `manifest.json` (NL
   request, spec, SQL, semantic version, key, allocation, counts, timestamps, assignment
   algorithm). Extra columns are fetched at download time and checked against your
   **current** export grants. Text cells starting with `= + - @`, TAB or CR get a leading
   `'` (spreadsheet formula guard), so `+4915…` appears as `'+4915…`.

The **History** tab (`cohort.create` or `cohort.read_all`) lists your runs — everyone's
with `cohort.read_all`. *Download again* (needs `cohort.export`) rebuilds the ZIP from the
stored membership snapshot (canonical id + group only; runs are never re-executed), so the
groups are identical; extra columns are fetched again under your current grants (a revoked
grant is refused — you can still download the ids only), and users deleted from the
warehouse keep their row with empty extra cells and are counted in the manifest. Someone
else's run is simply "not found" unless you hold `cohort.read_all`. Splits are audited as
`cohort.split`, downloads as `cohort.export` / `cohort.redownload`. Rulings:
[`docs/decisions/0004-experiment-split-rulings.md`](docs/decisions/0004-experiment-split-rulings.md).

## Campaign walkthrough (end to end, demo data)

From a clean clone on a machine with Docker, GNU make and curl:

1. **Configure.** `cp .env.example .env` and replace every `change-me-*` value. Choose an
   LLM:
   - *Real model, local:* install [Ollama](https://ollama.com), `ollama pull
     qwen2.5:7b-instruct`, keep `LLM_BASE_URL=http://host.docker.internal:11434/v1` and
     `LLM_MODEL=qwen2.5:7b-instruct`.
   - *Real model, cloud:* `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_MODEL=gpt-4o-mini`,
     `LLM_API_KEY=sk-…` (any OpenAI-compatible provider works the same way).
   - *No model (demo stub):* set `COMPOSE_PROFILES=demo`,
     `LLM_BASE_URL=http://llm-stub:8090/v1`, `LLM_MODEL=cohortsplit-demo-stub`. The stub
     is deterministic and only understands the requests used below; anything else gets a
     "use the README requests" clarification.
2. **Start.** `make up` (builds and waits until healthy), then create the first admin:
   `docker compose exec app cohortsplit create-admin --email admin@example.com`.
3. **Sign in** at `http://127.0.0.1:${APP_PORT}` (default port 8000).
4. **Allow phone export** (optional). *Roles → New role*: name `Phone export`, permission
   `cohort.export`, exportable column `public.users.phone`, member `admin@example.com`.
   (The Admin role holds every permission but no column grants.)
5. **Crawl.** *Semantic context → Crawler → Run crawler* (or `make crawl`).
6. **Business context.** *Semantic context → Business context → New entry*:
   - key `customer_id`, kind *Canonical user identifier*, definition
     `{"kind": "canonical_user_id", "column": "public.users.user_id"}`;
   - key `paid_order`, kind *Metric*, synonyms `paid for an order`, definition
     `{"kind": "metric", "table": "public.orders", "filters": [{"column": "public.orders.status", "operator": "in", "value": ["paid", "shipped", "delivered"]}]}`.
7. **AC-2 request.** *New cohort*: `Users from Kazakhstan who abandoned a cart and never
   paid for an order` → *Interpret*. The demo data has no Kazakh addresses, so you get a
   clarification naming the unknown value `KZ` (with a real model you may also get a
   question); nothing is executed. `Users from Germany who abandoned a cart and never paid
   for an order` interprets and previews as an **empty** cohort (the demo has no abandoned
   carts), so split and export stay disabled.
8. **Split.** *New cohort*: `Users who paid for an order` → *Interpret* → *Confirm and
   preview* (89 users) → experiment key `push_demo_2026`, 50/50, tick
   `public.users.phone` → *Split* → *Download ZIP*.
9. **History.** *History → Download again*: the same groups. Remove `public.users.phone`
   from the `Phone export` role and try again: the download is refused; *Download user ids
   only* still works.

`make down` stops the stack (`docker compose down -v` also deletes the data).

## Security notes

- Never commit `.env`; `.env.example` contains placeholders only.
- Ports are published on `127.0.0.1` only. **TLS termination is the deployer's
  responsibility** (put a TLS-terminating reverse proxy in front of `app`) and set
  `COHORTSPLIT_COOKIE_SECURE=true` when you do.
- Passwords are hashed with Argon2id. Sessions are server-side (HttpOnly,
  `SameSite=Strict` cookie; CSRF token required on every state-changing request) and
  permissions are evaluated on every request, so role changes apply immediately.
- Every endpoint enforces its own permission on the server; hidden tabs are UX only.
  Sensitive actions are written to an append-only audit log (database triggers refuse
  UPDATE/DELETE/TRUNCATE); if an audit write for a sensitive action fails, the action
  is refused.
- The warehouse role used by the app is read-only at the database level.
  Every statement also runs in a READ ONLY transaction with a timeout and row
  cap. Do not give that role access to `dblink`, `postgres_fdw` or functions
  with side effects: the role's privileges are the real guarantee.

## Layout

```text
backend/    FastAPI app (src/cohortsplit), Alembic, tests (unit, integration)
frontend/   React + TypeScript + Vite app (sign-in, dashboards)
docker/     warehouse and app images
docs/       product specs, plans, decisions
```
