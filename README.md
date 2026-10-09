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

## Layout

```text
backend/    FastAPI app (src/cohortsplit), Alembic, tests (unit, integration)
frontend/   React + TypeScript + Vite app (sign-in, dashboards)
docker/     warehouse and app images
docs/       product specs, plans, decisions
```
