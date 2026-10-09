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

## Security notes

- Never commit `.env`; `.env.example` contains placeholders only.
- Ports are published on `127.0.0.1` only. **TLS termination is the deployer's
  responsibility** (put a TLS-terminating reverse proxy in front of `app`).
- The warehouse role used by the app is read-only at the database level.

## Layout

```text
backend/    FastAPI app (src/cohortsplit), Alembic, tests (unit, integration)
frontend/   React + TypeScript + Vite app shell
docker/     warehouse and app images
docs/       product specs, plans, decisions
```
