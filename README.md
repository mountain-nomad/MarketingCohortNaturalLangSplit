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

The app is then at <http://127.0.0.1:8000> (`/api/health`, `/api/ready`, API docs
at `/api/docs`). `make down` stops the stack (data volumes are kept; use
`docker compose down -v` to reset them).

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
counts. It also generates example use cases (NL request → `draft-0` spec) from
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

## Security notes

- Never commit `.env`; `.env.example` contains placeholders only.
- Ports are published on `127.0.0.1` only. **TLS termination is the deployer's
  responsibility** (put a TLS-terminating reverse proxy in front of `app`).
- The warehouse role used by the app is read-only at the database level.
  Every statement also runs in a READ ONLY transaction with a timeout and row
  cap. Do not give that role access to `dblink`, `postgres_fdw` or functions
  with side effects: the role's privileges are the real guarantee.

## Layout

```text
backend/    FastAPI app (src/cohortsplit), Alembic, tests (unit, integration)
frontend/   React + TypeScript + Vite app shell
docker/     warehouse and app images
docs/       product specs, plans, decisions
```
