# Feature: Authentication, RBAC, Audit, and Dashboards

Status: draft — produced via `grill-me` on 2026-10-09. Part of the MVP (`docs/product/mvp.md`), delivered on `feature/authentication-rbac`.

## Problem

CohortSplit lets users extract customer cohorts — including phone numbers — from the company warehouse and change the business rules that define every cohort. Without identity and authorization, anyone who can reach the instance can export PII or silently redefine "purchase". The platform needs per-user accounts, admin-managed roles with arbitrary permission combinations, per-role control of exportable columns, and an audit trail of sensitive actions.

## Users

| Role | Description |
|---|---|
| **Admin** | Protected system role. Holds every permission, including permissions added in the future. Manages users, roles, grants; reads the audit log. Cannot be deleted or edited. |
| **Custom roles** | Created by the admin from the permission catalog, named freely (e.g. "Analyst", "Marketer", "KZ Marketing"). Business roles are data, never code. |
| **Server operator** | Anyone with shell access to the host. Can run CLI commands (create admin, reset password). Shell access is treated as fully trusted. |

## User Flow

### Bootstrap
```text
operator starts the app
  -> runs `cohortsplit create-admin --email admin@example.com`
  -> CLI prompts for a password (not echoed, not in shell history, not in env)
  -> admin logs in with email + password
```

### Onboarding a user
```text
admin -> Admin dashboard -> Users -> Create user (email, display name, roles, temporary password)
  -> admin hands the temporary password to the user out of band
  -> user logs in -> forced to set a new password before anything else
  -> user lands on the dashboard; tabs shown according to their permissions
```

### Managing roles
```text
admin -> Admin dashboard -> Roles -> Create role "Marketer"
  -> select permissions (checkboxes from the catalog)
  -> select exportable columns (e.g. users.phone) for this role
  -> assign role to users (from the role or from the user)
```

## Functional Requirements

### FR-A1 Authentication
- Email + password only. Email is unique, case-insensitive.
- Passwords hashed with a modern memory-hard algorithm (Argon2id); plaintext never stored or logged.
- Password policy: minimum 12 characters, maximum 128; no composition rules.
- Login failure messages never reveal whether the email exists.
- Brute-force protection: after 5 failed attempts for an account within 15 minutes, further attempts for that account are refused for 15 minutes (configurable). Applies equally to unknown emails.
- Sessions are server-validated. Idle timeout 8 hours, absolute lifetime 7 days (configurable). Logout invalidates the session server-side.
- Users can change their own password (requires current password). Changing password invalidates the user's other sessions.
- No self-signup. No email-based password reset in MVP (no SMTP).

### FR-A2 Users
- Admin creates users with email, display name, roles, and a temporary password.
- Temporary password forces a password change at next login; until changed, every endpoint except "change password" and "logout" is refused.
- Admin can edit display name and roles, reset a password (issues a new temporary password), deactivate and reactivate users.
- Users are **deactivated, never hard-deleted**, so audit history and run history remain attributable.
- Deactivation invalidates all sessions of that user immediately.

### FR-A3 Dynamic RBAC
- Model: `User ↔ Role ↔ Permission` many-to-many. A user's effective permissions are the **union** of permissions of all their roles.
- Admin can create, rename, edit, and delete custom roles with any combination of catalog permissions.
- The **Admin** role is a protected system role: not deletable, not editable, implicitly holds all permissions. It is assigned via the Users screen or CLI.
- **At least one active admin must always exist.** Deactivating, or removing the Admin role from, the last active admin is refused.
- Deleting a custom role that is assigned to users requires confirmation and removes those permissions from them immediately.
- Permission and role changes take effect on the user's next request (permissions are evaluated per request, never cached in the client or the session token).
- The permission catalog is defined in code/migrations; adding a custom role never requires a deployment.

MVP permission catalog:

| Area | Permissions |
|---|---|
| Users | `user.read`, `user.create`, `user.update`, `user.deactivate`, `user.reset_password` |
| Roles | `role.read`, `role.create`, `role.update`, `role.delete`, `role.assign` |
| Semantic layer | `semantic_context.read`, `semantic_context.edit`, `use_case.review`, `crawler.run` |
| Cohorts | `cohort.create` (interpret, preview, split), `cohort.export` (download / re-download), `cohort.read_all` (see everyone's runs) |
| Audit | `audit.read` |

Role-management permissions (`role.*`, `user.*`) are privilege-escalation vectors: a non-admin holding `role.update` + `role.assign` can grant themselves anything. In MVP this is allowed but the admin page warns when granting them to a custom role. See Open Question 2.

### FR-A4 Column export grants
- Each role holds a set of **exportable columns** (`schema.table.column`) chosen from columns discovered by the crawler.
- The canonical user ID is exportable by anyone with `cohort.export`; every other column requires a grant on one of the user's roles (union).
- Grants are checked on the backend at every export and re-download. Revoking a grant blocks subsequent exports of that column, including re-downloads of past runs.
- A column removed from the warehouse makes its grant inert; it is shown as "missing" in the role editor.

### FR-A5 Audit log
- Append-only. No UI or API can edit or delete audit events.
- Events recorded:
  - login success, login failure, lockout, logout, password change, forced password change;
  - user create / update / deactivate / reactivate / password reset;
  - role create / update / delete; role assignment and removal; permission and column-grant changes (with before/after);
  - cohort run (NL request, spec hash, SQL hash, semantic version, row count, experiment key, allocation);
  - export and re-download (run id, columns exported, row counts);
  - semantic-context edits, use-case review decisions, crawler runs.
- Each event: timestamp (UTC), actor user id (or `cli` for CLI actions), action, target, outcome (success / denied / error), request id, metadata.
- Events never contain passwords, session tokens, warehouse credentials, or exported row values (e.g. no phone numbers).
- **Denied actions are audited** (e.g. an export refused for missing grant).
- If an audit event for a sensitive action (export, re-download, permission change) cannot be written, the action fails (fail closed).
- Retention: indefinite in MVP.
- Admin dashboard **Audit** tab (requires `audit.read`): filter by actor, action, outcome, date range; paginated.

### FR-A6 Dashboards
One web app; tabs are shown according to effective permissions. Hiding a tab is UX only — every backend endpoint enforces its own permission.

**User dashboard**
| Tab | Requires | Content |
|---|---|---|
| New cohort | `cohort.create` | Request → interpretation → preview → split → download (feature `cohort-compiler` / `experiment-split`) |
| History | `cohort.create` or `cohort.read_all` | Past runs; re-download needs `cohort.export` (feature `experiment-split`) |
| Account | authenticated | Display name, change password |

**Admin dashboard**
| Tab | Requires | Content |
|---|---|---|
| Users | `user.read` | List, create, edit, deactivate, reset password |
| Roles | `role.read` | Roles, permissions, column export grants, assignments |
| Semantic context | `semantic_context.read` | Business context, generated docs, use-case review (feature `semantic-context`) |
| Audit | `audit.read` | Audit log viewer |

This feature delivers the dashboard shells, navigation, Account, Users, Roles, and Audit tabs. Other tabs are delivered by their features.

### FR-A7 CLI
- `cohortsplit create-admin --email <email>`: creates an active user with the Admin role, password prompted interactively. If the email exists, assigns the Admin role and reactivates the user (recovery path). Audited with actor `cli`.
- `cohortsplit reset-password --email <email>`: sets a new temporary password (prompted). Audited with actor `cli`.

### FR-A8 Forward compatibility
- Authorization decisions go through one backend policy layer so that **row-level security and table-level grants** (post-MVP) attach to roles without changing endpoint code.
- Column grants are modeled as role-attached data policies, the same family RLS predicates and table grants will join later.

## Business Rules

- BR-A1: All authorization is enforced on the backend; client-supplied roles, permissions, or user ids are ignored.
- BR-A2: Effective permissions = union over the user's active roles; Admin = all permissions.
- BR-A3: Effective export columns = canonical user ID ∪ grants of all the user's roles.
- BR-A4: Every permission check uses current state at request time; past grants confer nothing.
- BR-A5: There is always ≥ 1 active admin.
- BR-A6: Security-sensitive failures fail closed (auth store unreachable → deny; audit write failure on sensitive action → deny).

## Data Requirements

- Users: id, email (unique, case-insensitive), display name, password hash, must-change-password flag, active flag, created/updated timestamps, last login.
- Roles: id, unique name, description, system flag (Admin), timestamps.
- Role–permission, user–role, and role–export-column relations.
- Sessions: server-side record or revocable token with user id, creation, last activity, expiry.
- Login-attempt tracking for lockout.
- Audit events: append-only table.
- All stored in the application database, never in the customer warehouse.

## Permissions and Security

- Negative tests are mandatory for every permission-gated endpoint.
- CSRF protection for cookie-based sessions; cookies `HttpOnly`, `Secure` (when served over HTTPS), `SameSite`.
- No secrets, passwords, or tokens in logs or audit metadata.
- The README documents that TLS termination is the deployer's responsibility.

## Edge Cases

- Admin removes the Admin role from themselves while another active admin exists → allowed, takes effect next request.
- Last active admin tries to deactivate themselves → refused.
- Role renamed → assignments unchanged; audit records old/new name.
- Two roles with conflicting intent → no conflicts possible in MVP (union, no deny rules).
- User with zero roles → can log in, sees only Account tab.
- User deactivated mid-export → in-flight request may finish; next request refused.
- Duplicate email (case-insensitive) on create → validation error.
- Temporary password reused as new password → refused.
- Locked-out admin → wait out lockout or use CLI `reset-password`.

## Failure Behavior

| Failure | Behavior |
|---|---|
| App database unreachable | All authenticated requests refused with a service error; no fallback to cached permissions |
| Audit write fails on sensitive action | Action refused; error logged |
| Audit write fails on non-sensitive event (e.g. logout) | Action proceeds; failure logged |
| Session expired | Redirect to login; in-progress form state may be lost |
| Lockout | Generic message with retry-after time |

## Non-Goals (MVP)

- SSO / OIDC / SAML, MFA.
- Self-signup, invitations, email password reset.
- Row-level security, table-level grants, deny rules, PII auto-classification.
- Per-user permission overrides (permissions come only from roles).
- Multi-tenant organizations.
- Audit export / retention policies.

## Acceptance Criteria

### Authentication
- **AC-A1** Given a fresh install, when the operator runs `create-admin`, then that user can log in and has every permission.
- **AC-A2** Given a wrong password or an unknown email, the login response is identical in both cases.
- **AC-A3** Given 5 failed attempts within 15 minutes, the 6th attempt is refused even with the correct password, and a lockout event is audited.
- **AC-A4** Given a user with a temporary password, every endpoint except change-password and logout returns a must-change-password error.
- **AC-A5** Given a logged-out or expired session, any authenticated endpoint is refused.
- **AC-A6** Given a password change, the user's other sessions are invalidated.

### RBAC
- **AC-A7** Given a custom role with `cohort.create` only, the user cannot call any `user.*`, `role.*`, `audit.read`, or `cohort.export` endpoint (each tested directly against the API).
- **AC-A8** Given a user with roles R1 {`cohort.create`} and R2 {`cohort.export`}, the user can both create and export.
- **AC-A9** Given an admin removes `cohort.export` from a role, the user's next export request is refused.
- **AC-A10** Given a request carrying client-supplied role or permission claims, they are ignored.
- **AC-A11** The Admin role cannot be deleted or edited via API.
- **AC-A12** Deactivating or demoting the last active admin is refused.
- **AC-A13** Given a deactivated user, their existing sessions are refused on the next request.
- **AC-A14** Creating a new custom role and assigning it requires no deployment or restart.

### Column grants
- **AC-A15** Given no role of the user grants `users.phone`, an export or re-download requesting `phone` is refused and a denied audit event is recorded.
- **AC-A16** Given the grant is added, the same request succeeds; after revocation it is refused again.
- **AC-A17** The canonical user ID is exportable with `cohort.export` alone.

### Audit
- **AC-A18** Each event type listed in FR-A5 produces exactly one audit event with actor, action, target, outcome.
- **AC-A19** No API exists to update or delete audit events.
- **AC-A20** Audit events never contain passwords, tokens, credentials, or exported row values (asserted by scanning events produced during the test suite).
- **AC-A21** Given the audit store rejects writes, an export is refused.
- **AC-A22** Given a user without `audit.read`, the audit API is refused.

### Dashboards
- **AC-A23** A user's visible tabs match their effective permissions; hidden tabs' endpoints are still enforced server-side.
- **AC-A24** A user with zero roles sees only the Account tab.

## Open Questions

Defaults are assumed above; confirm or change.

1. **Lockout and session values.** Default: 5 attempts / 15 min lockout; 8h idle, 7-day absolute session. OK?
2. **Privilege escalation via role management.** A non-admin custom role holding `role.update` + `role.assign` can grant itself everything. Options: (a) allow, with a UI warning (default); (b) restrict `role.*` and `user.*` to the Admin role only, so only admins manage access.
3. **Can users see who else is on the platform?** Default: only holders of `user.read`.
