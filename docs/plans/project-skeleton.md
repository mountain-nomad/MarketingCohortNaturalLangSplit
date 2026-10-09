# Project Skeleton Implementation Plan

Branch: `feature/project-skeleton` · MVP Feature Breakdown row 1 · Implements FR-11 (demo environment) and seeds AC-12 (read-only DB user).

## Problem

The repository contains only product specs. There is no runnable backend, frontend, local environment, or CI, so no later feature (authentication, crawler, compiler, split) can be developed test-first.

## Desired behavior

A contributor can `git clone`, `cp .env.example .env`, `make up`, `make test`, and get:

- a FastAPI backend with `GET /api/health` (liveness, no DB) and `GET /api/ready` (readiness, fails closed with an actionable 503 when the app database is unreachable);
- typed settings loaded from `COHORTSPLIT_*` env vars, secrets held as `SecretStr` and never printed;
- Alembic wired to the app database with an empty baseline revision;
- a `cohortsplit` CLI entry point (commands arrive in later features);
- a React + TypeScript shell with a `CohortSplit` heading and a `/login` placeholder route;
- `docker compose` with three services: `warehouse` (PostgreSQL seeded with upstream `ecommerce` sample fetched at build time, pinned commit + checksum, plus read-only role `cohortsplit_ro`), `appdb` (PostgreSQL for app metadata), `app` (API + built frontend);
- CI running lint, type checks, unit tests and integration tests.

## Scope

- `backend/` (uv project, `src/cohortsplit/{app,config,db,cli}.py`, Alembic, unit + integration tests).
- `frontend/` (Vite React TS, react-router, Vitest, ESLint).
- `docker/warehouse/` (Dockerfile + init scripts), `docker/app/Dockerfile`, `docker-compose.yml`, `.env.example`, `Makefile`, `.github/workflows/ci.yml`, README quick start, `.gitignore` additions.

## Non-goals

- Authentication, RBAC, audit log, dashboards (`feature/authentication-rbac`).
- Warehouse adapter, crawler, SQL validation (`feature/warehouse-crawler`). The app does not connect to the warehouse yet; `COHORTSPLIT_WAREHOUSE_DSN` is accepted (optional) and only used by integration tests.
- TLS termination (deployer's responsibility), production hardening, app tables.

## Proposed interfaces

- `cohortsplit.config.Settings` (pydantic-settings, prefix `COHORTSPLIT_`):
  `appdb_host="localhost"`, `appdb_port=5432`, `appdb_name="cohortsplit"`, `appdb_user="cohortsplit"`, `appdb_password: SecretStr` (**required**), `warehouse_dsn: SecretStr | None = None`, `log_level="INFO"`, `frontend_dist: Path | None = None`.
- `cohortsplit.config.load_settings() -> Settings` — raises `ConfigError` whose message names each missing/invalid env var (e.g. `COHORTSPLIT_APPDB_PASSWORD`).
- `cohortsplit.db.appdb_url(settings) -> sqlalchemy.URL`, `create_appdb_engine(settings) -> Engine`, `check_appdb(engine) -> None` (raises `AppDatabaseUnavailable`).
- `cohortsplit.app.create_app(settings: Settings | None = None) -> FastAPI` — logs one startup line (no secrets).
  - `GET /api/health` → `200 {"status": "ok"}`.
  - `GET /api/ready` → `200 {"status": "ok", "checks": {"appdb": "ok"}}` or `503 {"status": "unavailable", "checks": {"appdb": "unavailable"}, "error": "<actionable message naming host:port/db and the COHORTSPLIT_APPDB_* settings>"}`.
  - When `frontend_dist` is set, serves the SPA (`index.html` fallback for client routes); `/api/*` never falls back.
- `cohortsplit.cli:main` exposed as `[project.scripts] cohortsplit`.

## Data-model changes

None beyond Alembic's `alembic_version` table. Baseline revision `0001_baseline` with empty `upgrade`/`downgrade`.

Warehouse (demo only): upstream `ecommerce.sql` loaded into DB `ecommerce` (16 tables in `public`: users, addresses, categories, products, product_variants, product_images, inventory, inventory_movements, carriers, carts, cart_items, orders, order_items, order_status_history, payments, shipments). Role `cohortsplit_ro`: `LOGIN`, no superuser/createdb/createrole, `CONNECT` + `USAGE` + `SELECT` only, `default_transaction_read_only=on`, `CREATE`/`TEMP` revoked from `PUBLIC`.

## Implementation steps

1. This plan (commit 1).
2. Tooling scaffolding + all tests below + `NotImplementedError` stubs; run, confirm RED; commit `test: ...` (commit 2).
3. Implement config → db/ready → app/health/SPA → CLI → Alembic → frontend → docker/compose/Makefile/CI until GREEN.
4. Completion gate, self-review, PR.

## Tests

| # | Test | Location |
|---|---|---|
| 1 | `/api/health` → 200 `{"status":"ok"}` without DB | `backend/tests/unit/test_health.py` |
| 2 | `/api/ready` → 503 actionable body when appdb port closed, no password in body; 200 when appdb reachable | `tests/unit/test_ready.py`, `tests/integration/test_ready_integration.py` |
| 3 | settings from env; missing required names the var; `SecretStr` hidden from `repr`/`str`; startup log has no password | `tests/unit/test_config.py`, `tests/unit/test_startup_log.py` |
| 4 | `alembic upgrade head` then `downgrade base` | `tests/integration/test_migrations.py` |
| 5 | warehouse tables present, `users` > 0 rows; as `cohortsplit_ro` SELECT works; INSERT/UPDATE/DELETE/CREATE TABLE/DROP refused, also after the session tries to switch to READ WRITE | `tests/integration/test_warehouse_readonly.py` |
| 6 | `cohortsplit --help` exits 0 via installed entry point | `tests/unit/test_cli.py` |
| 7 | App shell heading `CohortSplit`; `/login` renders placeholder | `frontend/src/App.test.tsx` |
| 8 | SPA serving: `/` and `/login` return `index.html`; `/api/unknown` is a JSON 404 | `tests/unit/test_spa.py` |

Integration tests are marked `integration`; they skip with a reason if the compose DBs are unreachable, unless `COHORTSPLIT_REQUIRE_INTEGRATION=1` (set by `make test-integration` and CI), in which case unreachability fails.

## Edge cases

- Closed port / wrong host for appdb → 503, never 500, never hangs (connect timeout).
- Required env var missing → startup refuses with the var name.
- Passwords with URL-special characters: appdb password is passed as a structured URL field (no encoding issue); the warehouse DSN must be URL-encoded (documented).
- Warehouse image rebuild: upstream file verified by SHA-256; mismatch fails the build.

## Security implications

- Secrets only from env; `.env` gitignored; `.env.example` contains placeholders only.
- `SecretStr` for passwords/DSN; startup log and 503 bodies exclude secrets (tested).
- Read-only enforcement at the database level via grants, not only `default_transaction_read_only` (a session can override that setting; grants still refuse writes — tested).
- Ports published on `127.0.0.1` only. TLS is the deployer's responsibility.
- Upstream SQL fetched at build time from a pinned commit and checksum-verified; not vendored (no upstream license).

## Acceptance criteria

- All tests in the table pass locally and in CI; RED commit precedes implementation commits.
- `make lint`, `make typecheck`, `make test`, `make test-integration` pass; `docker compose up` reports all three services healthy.
- No secrets committed; dependency versions pinned exactly (lock files committed).

## Definition of done

Completion gate from `AGENTS.md` satisfied with fresh output, self-review of `git diff origin/main...HEAD` done, PR opened against `main` with RED/GREEN evidence.
