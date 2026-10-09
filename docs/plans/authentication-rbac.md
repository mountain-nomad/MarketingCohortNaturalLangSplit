# Authentication, RBAC, Audit and Dashboards Implementation Plan

Branch: `feature/authentication-rbac` · MVP Feature Breakdown row 2 · Spec: `docs/product/authentication.md` (FR-A1..FR-A8, BR-A1..BR-A6, AC-A1..AC-A24) and MVP AC-29.

## Problem

CohortSplit exports customer cohorts (including PII such as phone numbers) and lets people change the business rules behind every cohort. The skeleton has no identity, no authorization and no audit trail: anyone who reaches the instance could export PII or redefine "purchase". The platform needs per-user accounts, admin-managed roles built from a permission catalog, per-role export-column grants, an append-only audit trail, and dashboards that show each user only what they may use.

## Desired behavior

- An operator bootstraps the first admin with `cohortsplit create-admin --email ...` (password prompted with no echo). The admin logs in with email + password.
- The admin creates users (temporary password, forced change on first login), custom roles from the grantable permission catalog, export-column grants per role, and assigns roles. The protected **Admin** system role holds every permission, including future ones.
- Every API endpoint enforces authentication and its own permission on the server, per request, from current database state. Hidden UI tabs are UX only.
- Sensitive actions are written to an append-only audit log; denied actions are audited too. If a sensitive audit write fails, the action fails.
- One web app: login, forced password change, dashboard whose tabs follow `GET /api/me` permissions; admin tabs Users, Roles, Audit; Account tab; placeholder tabs for features not yet delivered.

## Scope

Backend (`backend/src/cohortsplit/`):

- `orm.py` — shared SQLAlchemy `Base` and `UTCDateTime` type.
- `auth/` — `catalog.py` (permission catalog), `models.py`, `passwords.py` (Argon2id + policy), `sessions.py`, `lockout.py`, `service.py` (login, logout, password change), `users.py`, `roles.py`, `policy.py` (authorization service: effective permissions, export columns, export gate), `dependencies.py` (`get_db`, `current_principal`, `require_permission`), `errors.py`, `api.py` + `admin_api.py` (routers), `wiring.py` (`install_auth(app, settings, engine)`), `cli.py` (CLI subcommands), `clock.py`.
- `audit/` — `models.py` (`AuditEvent`), `actions.py` (action names), `service.py` (`AuditService.record`), `api.py` (read-only audit API).
- Alembic `0002_auth` (tables, seed of permission catalog and the Admin role, append-only trigger on `audit_events`).
- Small additive edits: `config.py` (auth settings), `app.py` (call `install_auth`, docs lockdown, optional injected engine), `cli.py` (register subcommands), `pyproject.toml`/`uv.lock` (`argon2-cffi`), README (auth quick start, TLS note).

Frontend (`frontend/src/`): API client with CSRF header, auth context, login page, change-password page, dashboard shell with permission-driven tabs, Account, Users, Roles, Audit tabs, "coming soon" tabs for New cohort / History / Semantic context.

## Non-goals

- SSO/OIDC/SAML, MFA, self-signup, invitations, email password reset (spec non-goals).
- RLS, table grants, deny rules, PII classification (FR-A8 only requires the policy layer they will attach to).
- Cohort runs, exports, re-downloads, semantic context, crawler (later features). This branch delivers the **export authorization gate** they must call; there is no export endpoint yet.
- Export endpoints themselves (feature `experiment-split`).
- Audit export/retention policies.

## Proposed interfaces

### Public backend interfaces for later features

```python
# cohortsplit.auth.dependencies
def get_db(request: Request) -> Iterator[Session]                       # one Session per request
def current_principal(...) -> Principal                                 # 401 / 403 password_change_required / CSRF on unsafe methods
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
def require_permission(permission: str) -> Callable[..., Principal]    # ValueError at import time for unknown permission; 403 + audited denial
def get_audit(request: Request) -> AuditService
def get_policy(request: Request) -> PolicyService

# cohortsplit.auth.policy
@dataclass(frozen=True)
class Principal:
    user_id: int; email: str; display_name: str; session_id: int
    is_admin: bool; role_ids: frozenset[int]; permissions: frozenset[str]
    def has(self, permission: str) -> bool

@dataclass(frozen=True)
class ExportColumnPolicy:
    canonical_user_id: bool                  # True iff cohort.export
    granted_columns: frozenset[str]          # "schema.table.column" union of the user's roles
    def allows(self, column: str) -> bool

class PolicyService:                         # the single policy layer (FR-A8)
    def effective_permissions(self, db: Session, user_id: int) -> frozenset[str]
    def effective_export_columns(self, db: Session, principal: Principal) -> ExportColumnPolicy
    def authorize_export(self, db: Session, principal: Principal, *, columns: Sequence[str],
                         run_id: str | None, redownload: bool, row_counts: Mapping[str, int] | None,
                         request_id: str | None, now: datetime) -> ExportColumnPolicy
        # raises PermissionDeniedError (missing cohort.export or a column grant; denial audited)
        # raises AuditWriteError when the success event cannot be written (fail closed)

# cohortsplit.audit.service
class AuditService:
    def record(self, db: Session, event: AuditEventIn, *, sensitive: bool = True) -> None
        # same transaction as the change; sensitive -> AuditWriteError on failure (caller's change rolls back)
        # non-sensitive -> savepoint, failure logged, request proceeds
    def record_detached(self, event: AuditEventIn, *, sensitive: bool) -> None
        # own transaction, committed immediately (denials, export gate)
@dataclass(frozen=True)
class AuditEventIn:
    action: str; outcome: Literal["success", "denied", "error"]; occurred_at: datetime
    actor_type: Literal["user", "cli", "anonymous"]; actor_user_id: int | None = None
    target_type: str | None = None; target_id: str | None = None
    request_id: str | None = None; metadata: Mapping[str, object] = {}
```

Export endpoints (feature `experiment-split`) must declare `require_permission("cohort.export")` and call `PolicyService.authorize_export(...)` immediately before producing data. The canonical user ID is not passed in `columns`: it is implicitly exportable with `cohort.export`.

### HTTP API

Error body everywhere: `{"error": {"code": "<code>", "message": "<text>", ...}}`. Request validation errors are `422 validation_error` without echoing input values (passwords must never be reflected).

| Method & path | Guard | Notes |
|---|---|---|
| `POST /api/auth/login` | public (JSON body) | 200 `{user, must_change_password, csrf_token}` + cookies; 401 `invalid_credentials` (identical for unknown email/wrong password/inactive); 429 `login_locked` + `Retry-After` |
| `POST /api/auth/logout` | session (also when must-change) | 204, session revoked, cookies cleared |
| `POST /api/auth/password` | session (also when must-change) | `{current_password, new_password}` → 204; 400 `invalid_current_password`; 422 `password_policy` / `password_reuse`; 429 when locked |
| `GET /api/me` | authenticated | `{id, email, display_name, is_admin, roles[], permissions[]}` |
| `GET /api/admin/users` | `user.read` | list |
| `POST /api/admin/users` | `user.create` (+`role.assign` when `role_ids`) | 201; 409 `email_taken`; 422 `password_policy`/`invalid_email`/`unknown_role` |
| `GET /api/admin/users/{id}` | `user.read` | 404 `not_found` |
| `PATCH /api/admin/users/{id}` | `user.update` | display name |
| `PUT /api/admin/users/{id}/roles` | `role.assign` | 409 `last_admin` |
| `POST /api/admin/users/{id}/deactivate` | `user.deactivate` | revokes sessions; 409 `last_admin` |
| `POST /api/admin/users/{id}/reactivate` | `user.deactivate` | |
| `POST /api/admin/users/{id}/reset-password` | `user.reset_password` | new temporary password; revokes sessions; clears lockout |
| `GET /api/admin/permissions` | `role.read` | catalog with `grantable` |
| `GET /api/admin/roles`, `GET /api/admin/roles/{id}` | `role.read` | |
| `POST /api/admin/roles` | `role.create` | 409 `role_name_taken`; 422 `permission_not_grantable`/`unknown_permission`/`invalid_column` |
| `PATCH /api/admin/roles/{id}` | `role.update` | 403 `system_role_protected` for Admin |
| `DELETE /api/admin/roles/{id}?confirm=true` | `role.delete` | 409 `confirmation_required` when assigned; 403 for Admin |
| `PUT /api/admin/roles/{id}/members` | `role.assign` | custom roles only (Admin is assigned from Users / CLI) |
| `GET /api/audit/events` | `audit.read` | filters `actor_email`, `actor_type`, `action`, `outcome`, `since`, `until`; `limit` (1..200, default 50), `offset` |
| `GET /api/docs`, `GET /api/openapi.json` | disabled by default (404); when `COHORTSPLIT_API_DOCS_ENABLED=true`, authenticated session required |

Status codes: 401 `not_authenticated`; 403 `permission_denied` / `password_change_required` / `csrf_failed`; 503 `service_unavailable` (app DB unreachable), 503 `audit_unavailable` (sensitive audit write failed).

### Sessions and CSRF

- Login creates a server-side session: random 256-bit token in cookie `cohortsplit_session` (`HttpOnly`, `SameSite=Strict`, `Path=/api`, `Secure` when `COHORTSPLIT_COOKIE_SECURE=true`, or when unset and the request is HTTPS). Only the SHA-256 of the token is stored.
- CSRF: synchronizer token bound to the session. A second random token is returned in the login body and in cookie `cohortsplit_csrf` (readable by JS, `SameSite=Strict`); its SHA-256 is stored on the session. Every authenticated `POST/PUT/PATCH/DELETE` must send header `X-CSRF-Token` matching the session (constant-time compare) or gets 403 `csrf_failed`. Login requires a JSON body (no CORS-simple form posts).
- Validity per request: not revoked, `now < expires_at` (absolute 7 days), `now - last_seen_at < idle` (8 hours), user active. `last_seen_at` is refreshed at most once per minute.

### CLI

`cohortsplit create-admin --email E` and `cohortsplit reset-password --email E`, registered from `cohortsplit/auth/cli.py`. Passwords only via `getpass` (prompt + confirmation); there is no `--password` flag. Both audited with actor `cli`.

- create-admin, new email: active user, Admin role, prompted password, no forced change.
- create-admin, existing email (recovery path): assigns Admin and reactivates; password unchanged (use `reset-password`).
- reset-password: temporary password (forced change), revokes the user's sessions, clears lockout.

### Settings (`COHORTSPLIT_*`)

`session_idle_timeout_minutes=480`, `session_max_lifetime_hours=168`, `login_max_failures=5`, `login_failure_window_minutes=15`, `login_lockout_minutes=15`, `cookie_secure: bool | None = None` (auto), `api_docs_enabled=False`.

## Data-model changes (Alembic `0002_auth`, down_revision `0001_baseline`)

| Table | Columns / constraints |
|---|---|
| `users` | `id bigint identity PK`, `email text UNIQUE CHECK (email = lower(email))`, `display_name text` (1..200), `password_hash text`, `must_change_password bool`, `is_active bool`, `created_at`, `updated_at`, `last_login_at` (timestamptz) |
| `roles` | `id`, `name text` (1..100, unique on `lower(name)`), `description text`, `is_system bool` (partial unique: at most one system role), timestamps |
| `permissions` | `key text PK`, `description text` — seeded from the catalog |
| `role_permissions` | `(role_id → roles ON DELETE CASCADE, permission_key → permissions)` PK, index on `permission_key` |
| `user_roles` | `(user_id → users, role_id → roles ON DELETE CASCADE)` PK, index on `role_id` |
| `role_export_columns` | `(role_id → roles ON DELETE CASCADE, column_ref text)` PK |
| `auth_sessions` | `id`, `token_hash text UNIQUE`, `csrf_token_hash text`, `user_id → users` (indexed), `created_at`, `last_seen_at`, `expires_at`, `revoked_at` |
| `login_throttles` | `email text PK`, `failure_count int`, `window_started_at`, `locked_until`, `updated_at` |
| `audit_events` | `id`, `occurred_at`, `actor_type text CHECK`, `actor_user_id → users` (indexed), `action`, `target_type`, `target_id`, `outcome text CHECK`, `request_id`, `metadata jsonb`; indexes on `occurred_at`, `action`; `BEFORE UPDATE OR DELETE` row trigger and `BEFORE TRUNCATE` statement trigger raise (append-only at DB level) |

Seed: permission catalog (18 keys from FR-A3) and the `Admin` system role. Admin's permissions are **not** rows: `is_system` implies the full code catalog, so future permissions are included automatically. Downgrade drops everything (including the trigger function).

## Implementation steps

1. Commit this plan.
2. **Slice 1 – auth core** (RED commit, then GREEN): ORM base/models + stubs; tests for passwords, login, lockout, sessions, CSRF, must-change gating, `/api/me`, CLI, docs lockdown, migration/schema integration. Implement `0002_auth`, services, dependencies, wiring, CLI.
3. **Slice 2 – RBAC, grants, audit** (RED, then GREEN): tests for users/roles admin APIs, last-admin rules, admin-only permissions, export gate, audit API and event coverage, endpoint security matrix. Implement.
4. **Slice 3 – frontend** (RED, then GREEN): Vitest tests for tab visibility, login, forced change, Users/Roles/Audit forms. Implement.
5. README + docs; completion gate (`make lint typecheck test`, `make up`, `make test-integration`, CLI + login smoke in the container); review; fixes test-first; PR.

## Tests

Backend API tests live in `backend/tests/auth/` and run against **two backends**: SQLite in-memory (unit run) and a fresh, migrated PostgreSQL database created per test session on the compose appdb (marked `integration`, so `make test-integration` and CI re-run the whole suite on Postgres through `alembic upgrade head`). Argon2 uses cheap parameters in tests; a separate test pins production parameters.

### AC → test map

| AC | Test(s) |
|---|---|
| AC-A1 | `test_cli_auth.py::test_create_admin_user_can_log_in_and_has_every_permission` |
| AC-A2 | `test_login.py::test_wrong_password_and_unknown_email_get_identical_responses` |
| AC-A3 | `test_login.py::test_sixth_attempt_refused_even_with_correct_password_and_lockout_audited`, `::test_lockout_applies_to_unknown_emails`, `::test_lockout_expires_after_configured_minutes` |
| AC-A4 | `test_passwords.py::test_must_change_password_blocks_every_other_endpoint` |
| AC-A5 | `test_sessions.py::test_logout_invalidates_session_server_side`, `::test_idle_timeout_expires_session`, `::test_absolute_lifetime_expires_session_despite_activity` |
| AC-A6 | `test_passwords.py::test_password_change_invalidates_other_sessions_keeps_current` |
| AC-A7 | `test_endpoint_security.py::test_cohort_create_only_user_refused_everywhere_else`, `::test_every_gated_endpoint_refuses_unauthenticated`, `::test_every_gated_endpoint_refuses_missing_permission` |
| AC-A8 | `test_rbac_roles.py::test_effective_permissions_are_union_of_roles` |
| AC-A9 | `test_export_policy.py::test_removing_cohort_export_from_role_refuses_next_export` |
| AC-A10 | `test_rbac_roles.py::test_client_supplied_role_and_permission_claims_are_ignored` |
| AC-A11 | `test_rbac_roles.py::test_admin_role_cannot_be_edited`, `::test_admin_role_cannot_be_deleted` |
| AC-A12 | `test_rbac_users.py::test_last_active_admin_cannot_be_deactivated`, `::test_last_active_admin_cannot_be_demoted` |
| AC-A13 | `test_rbac_users.py::test_deactivated_user_sessions_refused_on_next_request` |
| AC-A14 | `test_rbac_roles.py::test_new_role_takes_effect_without_restart` |
| AC-A14a | `test_rbac_roles.py::test_custom_role_with_admin_only_permission_refused` (create + update), frontend `RolesTab.test.tsx` "does not offer admin-only permissions" |
| AC-A14b | `test_rbac_users.py::test_non_admin_cannot_list_users` |
| AC-A15 | `test_export_policy.py::test_export_of_ungranted_column_refused_and_denial_audited` |
| AC-A16 | `test_export_policy.py::test_grant_then_revoke_column` |
| AC-A17 | `test_export_policy.py::test_canonical_user_id_exportable_with_cohort_export_alone` |
| AC-A18 | `test_audit.py::test_each_event_type_recorded_exactly_once` |
| AC-A19 | `test_audit.py::test_no_api_mutates_audit_events`, integration `test_auth_schema.py::test_audit_events_are_append_only_in_database` |
| AC-A20 | `test_audit.py::test_audit_events_never_contain_secrets` |
| AC-A21 | `test_export_policy.py::test_export_refused_when_audit_store_rejects_writes` |
| AC-A22 | `test_audit.py::test_audit_api_requires_audit_read` |
| AC-A23 | frontend `tabs.test.ts`, `App.test.tsx` "shows tabs matching permissions"; backend `test_endpoint_security.py` |
| AC-A24 | frontend `App.test.tsx` "user with zero roles sees only the Account tab"; `test_rbac_users.py::test_user_with_zero_roles_logs_in_with_no_permissions` |
| MVP AC-29 | `test_endpoint_security.py` (route inventory + matrices) |

### Other tests

- Passwords: Argon2id hash format, production parameters, policy 12..128 characters, no composition rules.
- Sessions: tokens stored hashed, cookie flags, `Secure` toggles, CSRF missing/wrong/valid, garbage cookie, DB unreachable → 503.
- Users/roles edge cases: duplicate email case-insensitive, self-demotion with another admin, role rename audited old/new, role delete confirmation, unknown permission, invalid column format, zero roles, reactivation, reset password.
- Audit: filters, pagination, request id, sensitive change rolled back when audit fails, denied permission checks audited.
- CLI: no `--password` flag, getpass prompts, confirmation mismatch, policy, recovery path, actor `cli`.
- Integration: migration upgrade/downgrade, seeded catalog equals code catalog, ORM models match migration, DB-level append-only audit, case-insensitive uniqueness.
- Frontend (Vitest): tab visibility matrix, redirect to login, login error, forced change, Account change-password form, Users create, Roles editor (admin-only perms not offered, payload), Audit filters/pagination.

## Edge cases

Admin removes Admin from themselves while another active admin exists → allowed. Last active admin deactivates/demotes themselves → 409 (serialized by `SELECT ... FOR UPDATE` on the Admin role row). Role renamed → assignments unchanged, audit has old/new name. User with zero roles → can log in, only Account tab. Deactivated mid-request → next request refused. Duplicate email in any case → 409. Temporary password reused as new password → 422 `password_reuse`. Locked-out admin → wait or CLI `reset-password` (clears lockout). Unknown email login → same response, lockout tracked the same. Role delete with members → requires `confirm=true`, permissions gone on next request.

## Security implications

- Argon2id (argon2-cffi defaults: t=3, m=64 MiB, p=4); dummy verification for unknown emails to equalize timing.
- No passwords, session/CSRF tokens, or hashes in logs, audit metadata or API responses; audit metadata keys that look secret are redacted; login failures for unknown emails store only a truncated SHA-256 fingerprint (users sometimes type a password into the email field).
- Session tokens hashed at rest; permissions evaluated per request from the database (never cached in the client or the session).
- Fail closed: DB unreachable → 503; sensitive audit write failure → action refused (changes in the same transaction roll back); unknown permission names in `require_permission` fail at import time.
- `user.*`/`role.*` are not grantable to custom roles (privilege escalation closed).
- API docs disabled by default.
- TLS termination remains the deployer's responsibility (README); set `COHORTSPLIT_COOKIE_SECURE=true` behind HTTPS.

## Rulings (fail-closed choices where the spec is silent)

1. Admin does **not** implicitly hold column export grants: column grants are role-attached data policies, not permissions; an admin who needs `phone` grants it to a role they hold (audited).
2. Login failure for an inactive account returns the same generic `invalid_credentials` response.
3. Account tab shows display name read-only; only admins edit display names (FR-A2).
4. `create-admin` on an existing email does not change the password.
5. Wrong current password on change-password counts toward the account lockout.
6. API docs disabled unless `COHORTSPLIT_API_DOCS_ENABLED=true`; even then require a session.
7. Audit reader responses include the actor's email (identity is the point of an audit trail; `audit.read` is an explicit grant).
8. Denied permission checks are audited (`access.denied`, best effort).
9. Admin role membership changes only through the Users screen/API or CLI (`PUT /roles/{id}/members` refuses the system role).
10. Migration id `0002_auth` is kept after re-chaining it onto `0003_warehouse_metadata` (single head `0002_auth`); ids are opaque and renaming would orphan development databases stamped with it. Development databases migrated before the merge must be reset (`docker compose down -v`).
11. "Missing" export grants (FR-A4): a grant is missing when its column is absent from the generated `table_schema` docs of the crawler (the latest state of every crawled schema). Before any crawl the status is `unavailable` and nothing is marked. Missing grants can still be saved (prepared for a future column) and stay inert.
12. Crawler hooks: `cohortsplit crawl` passes `RoleExportGrants` (union of every role's grants, assigned or not) and `AuditCrawlHook` (one `crawler.run` event per run, non-sensitive). Samples stored before a grant was added are not purged on grant changes; they disappear at the next crawl. Re-crawl after adding a grant (open concern; an automatic purge is follow-up work).

## Acceptance criteria

All AC-A1..AC-A24 and MVP AC-29 covered by the tests mapped above, passing on SQLite and PostgreSQL; `make lint`, `make typecheck`, `make test`, `make test-integration` green; migration upgrades and downgrades cleanly; app container healthy; CLI-created admin can log in via the API in the container.

## Definition of done

Tests committed before implementation in each slice (RED evidence in `test:` commit bodies); completion gate from `AGENTS.md` satisfied with fresh output; independent code review findings (Critical/Important) fixed test-first; README updated; PR opened against `main` with AC→test map and RED/GREEN evidence; CI green.
