---
name: access-control
description: Security model for authentication, protected platform administration, dynamic RBAC, datasource access, RLS, column/PII security, secret management, and audit logging. Use for any feature touching users, permissions, protected data, exports, or datasource configuration.
---

# Access Control

## Security layers

Keep these distinct:

```text
Authentication       Who are you?
RBAC                 What actions may you perform?
Datasource Access    Which datasource/schema/table may you use?
RLS/Column Security  Which rows/columns may you see/export?
```

All enforcement occurs on the backend. Frontend checks are UX only.

## Platform administrator

The platform has a protected system-level administration capability.

Only administrators may manage by default:

- users;
- custom roles;
- permission assignments;
- datasource connections/credentials;
- datasource grants;
- RLS policies;
- column-sensitive/PII classification.

The protected admin capability must not be accidentally removable by deleting a normal custom role.

## Dynamic RBAC

Business roles are data, not code.

Never implement:

```python
if user.role == "marketing":
    ...
```

Use dynamic many-to-many relationships:

```text
User -> UserRole -> Role -> RolePermission -> Permission
```

Permissions should be atomic/composable, e.g.:

```text
user.read
user.create
user.deactivate
role.read
role.create
role.update
role.delete
role.assign
datasource.read
datasource.create
datasource.update
datasource.delete
datasource.assign
cohort.create
cohort.read
cohort.execute
cohort.export
cohort.delete
experiment.create
experiment.read
experiment.export
semantic_context.read
semantic_context.edit
rls.read
rls.create
rls.update
rls.delete
audit.read
pii.read
pii.export
```

The precise permission catalog belongs in migrations/code, but adding a custom role must not require a deployment.

## Datasource configuration

Datasources are administrator-managed shared resources. Ordinary users receive grants; they do not own plaintext DB credentials.

A datasource model may include:

- id/name/type;
- host/port/database/user metadata;
- secret reference or encrypted credential payload;
- SSL config;
- creator/timestamps.

Never log plaintext secrets or full credential-bearing connection URLs.

## Secret management

For self-hosted MVP, encrypted-at-rest credentials may be acceptable when the master key comes from environment/runtime secret configuration.

Architecture should permit future secret-manager adapters such as Vault or cloud secret managers.

## RLS

RLS is independent of RBAC.

Example policy:

```text
Role: Kazakhstan Marketing
Datasource: Production Warehouse
Table: customers
Predicate: country_code = 'KZ'
```

RLS must be applied deterministically after compilation and before execution. Never ask the LLM to remember or generate security predicates.

Define and test policy-composition semantics explicitly, especially when multiple roles and user overrides coexist.

## Column security / PII

Allow metadata classification of sensitive columns such as email, phone, address, national identifiers, etc.

Automated classification may suggest labels, but administrators must be able to confirm/correct them.

Distinguish reading from exporting sensitive data when required, e.g. `pii.read` vs `pii.export`.

## User settings

Preferences (default datasource/model/export format/experiment defaults) never override authorization.

## Audit logging

Audit sensitive operations including:

- cohort execution/export;
- experiment creation/export;
- datasource creation/change;
- user/role/permission changes;
- RLS changes;
- sensitive-data policy changes.

Never include secrets or unnecessary PII in audit events.

## Negative security tests

Always add tests such as:

- unauthorized user cannot export;
- KZ-scoped role cannot retrieve UAE rows;
- user without PII export cannot export email;
- frontend-supplied role/permission claims are ignored;
- revoked datasource grant blocks subsequent execution.

Security-sensitive behavior must fail closed.
