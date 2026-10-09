#!/usr/bin/env bash
set -Eeuo pipefail

# Cohort Platform agent/skills bootstrap.
# Usage:
#   chmod +x bootstrap-agents.sh
#   ./bootstrap-agents.sh [repo-root]
#
# What it does:
# - creates/rewrites AGENTS.md
# - creates project-specific skills under .agents/skills/
# - vendors selected high-signal upstream skills from GitHub
# - preserves upstream licenses
# - records exact upstream commit SHAs in .agents/skills-lock.md
# - creates docs/plans, docs/product, docs/decisions
# - adds safe local-secret patterns to .gitignore
# - validates that all expected SKILL.md files exist

ROOT="${1:-$PWD}"
ROOT="$(cd "$ROOT" && pwd)"
cd "$ROOT"

BOOTSTRAP_MARKER="COHORT_PLATFORM_AGENT_BOOTSTRAP_V1"
AGENTS_DIR="$ROOT/.agents"
SKILLS_DIR="$AGENTS_DIR/skills"
LICENSE_DIR="$AGENTS_DIR/vendor-licenses"
DOCS_DIR="$ROOT/docs"
TMP_DIR="$(mktemp -d)"
BACKUP_DIR="$ROOT/.bootstrap-backups"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
fail() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command '$1' is not installed."
}

require_cmd git
require_cmd grep
require_cmd cp
require_cmd mkdir
require_cmd mktemp
require_cmd date

say "Preparing repository at $ROOT"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  warn "No git repository detected; initializing one."
  git init >/dev/null
fi

mkdir -p "$SKILLS_DIR" "$LICENSE_DIR" "$DOCS_DIR/plans" "$DOCS_DIR/product" "$DOCS_DIR/decisions" "$ROOT/scripts"

# Keep the bootstrap in the repository so upstream skills can be intentionally refreshed later.
SCRIPT_SOURCE_ABS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
TARGET_SCRIPT="$ROOT/scripts/bootstrap-agents.sh"
if [[ "$SCRIPT_SOURCE_ABS" != "$TARGET_SCRIPT" ]]; then
  cp "$SCRIPT_SOURCE_ABS" "$TARGET_SCRIPT"
  chmod +x "$TARGET_SCRIPT"
fi

backup_if_user_managed() {
  local path="$1"
  [[ -e "$path" ]] || return 0
  if grep -q "$BOOTSTRAP_MARKER" "$path" 2>/dev/null; then
    return 0
  fi
  mkdir -p "$BACKUP_DIR"
  local safe_name
  safe_name="$(printf '%s' "${path#$ROOT/}" | tr '/' '_')"
  cp -a "$path" "$BACKUP_DIR/${safe_name}.${TIMESTAMP}"
  warn "Backed up existing $path to $BACKUP_DIR/${safe_name}.${TIMESTAMP}"
}

backup_if_user_managed "$ROOT/AGENTS.md"

say "Writing AGENTS.md"
cat > "$ROOT/AGENTS.md" <<'EOF'
<!-- COHORT_PLATFORM_AGENT_BOOTSTRAP_V1 -->
# AGENTS.md

## Project

This repository contains a self-hosted customer cohort and experimentation platform.

A user should be able to clone the repository, configure a supported data warehouse and an LLM provider, start the application, and create reusable customer audiences from natural-language business intent.

Example:

> Users who liked product `12345` or joined through referral campaign `GET100`.

This is **not a generic NL-to-SQL chatbot**.

Core flow:

```text
Natural Language
    -> Structured Cohort Specification
    -> Deterministic SQL Compiler
    -> Authorization / Data Policies
    -> SQL Validation
    -> DWH
    -> Cohort
    -> Save / Export / A-A / A-B
```

The platform must support local LLMs as well as remote providers and must not require MCP.

## Skill-first development

Detailed instructions intentionally live in skills. Before doing substantial work, determine which skills apply and read them.

Project skills:

- `grill-me`
- `cohort-platform-domain`
- `warehouse-semantic-layer`
- `cohort-compiler`
- `access-control`
- `experimentation`

External engineering skills are vendored under `.agents/skills/` from upstream GitHub repositories. Exact source commits are recorded in `.agents/skills-lock.md`.

## Routing

### Requirements unclear or feature not fully specified
Use `grill-me` before planning or coding. Do not guess product decisions with meaningful UX, data, security, or architectural consequences.

### Product behavior / cohort lifecycle
Use `cohort-platform-domain`.

### DWH adapters / metadata crawler / semantic documentation
Use `warehouse-semantic-layer`.

### Natural language interpretation / cohort specification / SQL compilation
Use `cohort-compiler`.

### Authentication / admin / RBAC / datasource permissions / RLS / PII / secrets
Use `access-control`.

### A/A and A/B experiments / sample size / MDE / deterministic assignment
Use `experimentation`.

### Planning, TDD, debugging, review, branch completion
Use the relevant vendored Superpowers skills, especially:

- `writing-plans`
- `test-driven-development`
- `systematic-debugging`
- `verification-before-completion`
- `requesting-code-review`
- `using-git-worktrees`
- `executing-plans`
- `finishing-a-development-branch`

### React / UI
Use the Vercel skills:

- `frontend-design`
- `vercel-react-best-practices`
- `vercel-composition-patterns`

### Backend / Python / API / persistence
Use the relevant backend skills:

- `api-design-principles`
- `fastapi-templates`
- `python-testing-patterns`
- `auth-implementation-patterns`
- `postgresql-table-design`
- `error-handling-patterns`

## Mandatory feature workflow

Every meaningful feature must have its own feature branch, for example:

```text
feature/authentication
feature/rbac
feature/datasource-management
feature/warehouse-crawler
feature/cohort-compiler
feature/experimentation-engine
```

Do not mix unrelated major features in one branch.

Before implementation, create:

```text
docs/plans/<feature-name>.md
```

The plan must include:

- problem;
- desired behavior;
- scope;
- non-goals;
- proposed interfaces;
- data-model changes;
- implementation steps;
- tests;
- edge cases;
- security implications;
- acceptance criteria;
- definition of done.

If product behavior is not sufficiently clear, run `grill-me` first and create/update `docs/product/<feature-name>.md` before the implementation plan.

## Test-first rule

New production behavior must be represented by a failing test before implementation.

```text
RED -> failing test
GREEN -> minimum implementation
REFACTOR -> improve structure without changing behavior
```

Never weaken a valid test merely to make implementation pass.

Security-sensitive features require explicit negative tests.

## Core architectural boundaries

Keep these concerns independent:

```text
Authentication
Authorization / RBAC
Datasource Access
RLS
Column Security

Warehouse Metadata
Business Semantics

Natural Language Interpretation
Cohort Specification
SQL Compilation
SQL Execution

Experiment Statistics
Experiment Assignment
Export
```

Do not collapse these layers for convenience.

## LLM boundary

The LLM is an interpreter, not a security or execution authority.

Preferred:

```text
Natural language
    -> LLM
    -> Structured cohort specification
    -> Deterministic application code
    -> SQL
```

Do not use arbitrary LLM-generated executable SQL as the trusted system boundary.

LLMs must not be responsible for:

- RBAC or permission enforcement;
- RLS;
- column security;
- secret handling;
- experiment assignment;
- destructive database operations.

## Database safety

Generated warehouse queries must be read-only. Reject mutation/administration statements such as:

- `INSERT`
- `UPDATE`
- `DELETE`
- `DROP`
- `ALTER`
- `TRUNCATE`
- `CREATE`
- `GRANT`
- `REVOKE`

Enforce where applicable:

- query timeouts;
- result limits;
- schema/table allowlists;
- restricted columns;
- SQL AST validation;
- datasource authorization;
- RLS.

Never expose warehouse credentials to the LLM.

## Code quality

Prefer:

```text
explicit > clever
small cohesive modules > generic mega-abstractions
composition > unnecessary inheritance
deterministic behavior > hidden randomness
existing abstraction > duplicate implementation
```

Do not hardcode business roles, customer schemas, DWH table names, model providers, credentials, or user-specific access conditions.

Use typed interfaces where practical, structured logging, actionable errors, and minimal pinned dependencies.

Security-sensitive code must fail closed.

## Completion gate

Before claiming a feature is complete:

1. run relevant unit tests;
2. run integration tests where applicable;
3. run security/authorization tests where applicable;
4. run linting and type checks;
5. verify database migrations;
6. verify acceptance criteria from the feature plan;
7. use `verification-before-completion`;
8. perform code review;
9. update affected documentation.

Do not claim completion from inspection alone. Provide fresh evidence.

## Source-of-truth priority

When instructions conflict:

1. explicit current product-owner requirement;
2. approved feature/product specification;
3. project-specific skill;
4. this `AGENTS.md`;
5. external engineering skill;
6. existing implementation convention.

Security constraints must never be silently weakened by a lower-priority instruction.
EOF

write_skill() {
  local name="$1"
  local dir="$SKILLS_DIR/$name"
  rm -rf "$dir"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md"
}

say "Writing project-specific skills"

write_skill "grill-me" <<'EOF'
---
name: grill-me
description: Deeply interview the product owner before planning substantial features. Use when requirements, UX, business rules, data semantics, security behavior, edge cases, or acceptance criteria are materially unclear, or when the user explicitly asks to be grilled.
---

# Grill Me

## Purpose

Eliminate important assumptions before architecture or implementation. Act as a senior product engineer, not a passive requirements collector.

The goal is to obtain enough decision-grade context that an engineer can implement the feature without repeatedly returning to the product owner.

## Before asking questions

1. Read `AGENTS.md`.
2. Read relevant project skills.
3. Inspect existing product docs, plans, code, tests, and interfaces.
4. Do not ask questions whose answers are already present in the repository.
5. Identify the highest-risk unknowns first.

## Interview method

Start with a short summary of your current understanding, maximum five bullets.

Then ask focused high-information questions. Prefer one question or one coherent batch at a time. Follow answers and drill deeper only where the answer changes behavior or architecture.

Prioritize:

1. user and problem;
2. desired end-to-end workflow;
3. expected result;
4. business rules;
5. permissions/security;
6. data semantics;
7. failure behavior;
8. edge cases;
9. UX expectations;
10. scale/performance;
11. observability/auditability;
12. explicit non-goals.

Do not mechanically ask every category.

## Ask for examples

Prefer concrete examples over abstractions.

Useful questions include:

- Give two requests that must work and one that must be rejected.
- What exact result should the user see?
- What should happen if the request is ambiguous?
- What happens if the cohort is larger than the export limit?

Examples should later become tests.

## Expose ambiguity

When two interpretations are plausible, state both and ask the owner to choose.

Example:

> If a user has two roles with different RLS policies, should effective row access be the union or intersection of those policies?

Never silently choose a consequential interpretation.

## Expose contradictions

If a new requirement conflicts with an existing invariant, say so explicitly.

Example:

> The platform is currently defined as read-only against customer DWHs, but this feature proposes writing experiment assignments to the DWH. Should assignments remain in the application database, or is controlled DWH write access now allowed?

## Failure modes

For important features, determine expected behavior for relevant failures, such as:

- DWH unavailable;
- LLM unavailable;
- ambiguous natural-language request;
- cohort cannot be resolved;
- SQL validation failure;
- permission changes after cohort save;
- semantic context changes;
- datasource deletion;
- insufficient experiment population.

## Security questions

When data or permissions are involved, establish:

- who can perform the action;
- who can see the result;
- who can export it;
- whether PII can be exposed;
- whether the action must be audited;
- whether saved artifacts inherit later permission changes;
- whether access comes from role, user override, or both.

## Data questions

Establish when relevant:

- canonical entity and identifier;
- source-of-truth tables;
- time semantics;
- NULL behavior;
- duplicate behavior;
- refresh/reproducibility behavior;
- expected scale.

## UX questions

Establish workflow before visuals:

- entry point;
- primary action;
- preview/confirmation;
- editable vs immutable fields;
- defaults;
- error behavior;
- empty states;
- export/download behavior.

## Stop condition

Stop only when these are sufficiently clear:

```text
WHO        Who uses it?
WHY        What problem does it solve?
FLOW       What happens end-to-end?
RULES      Which business rules govern it?
ACCESS     Who may perform and see what?
DATA       Which data semantics matter?
FAILURES   What happens when things go wrong?
ACCEPTANCE How do we know it is correct?
NON-GOALS  What are we deliberately not building?
```

## Output

Create or update:

```text
docs/product/<feature-name>.md
```

Use:

```text
# Feature
## Problem
## Users
## User Flow
## Functional Requirements
## Business Rules
## Data Requirements
## Permissions and Security
## Edge Cases
## Failure Behavior
## Non-Goals
## Acceptance Criteria
## Open Questions
```

Acceptance criteria must be testable. Prefer Given/When/Then for security and edge cases.

After the spec is sufficiently clear, hand off to the planning workflow. `grill-me` determines **what** should be built; planning determines **how**.
EOF

write_skill "cohort-platform-domain" <<'EOF'
---
name: cohort-platform-domain
description: Product and domain rules for the self-hosted customer cohort platform. Use when changing cohort lifecycle, saved cohorts, exports, user-facing workflows, platform invariants, terminology, or product behavior.
---

# Cohort Platform Domain

## Product intent

Build an out-of-the-box, self-hosted platform that a team can clone, configure, and run against its own data warehouse.

The platform lets business users describe audiences in natural language and receive reusable customer cohorts without writing SQL.

It is not a generic analytics chatbot and does not answer arbitrary analytical questions by default. Its primary output is an audience/cohort represented by a canonical user identifier plus allowed export fields.

## Primary user journey

```text
Admin connects datasource
    -> metadata crawler discovers warehouse
    -> semantic/business context is reviewed
    -> business user describes cohort
    -> structured cohort specification is produced
    -> deterministic SQL is compiled and secured
    -> cohort is previewed
    -> user saves / refreshes / exports / experiments
```

## Core product capabilities

- self-hosted setup;
- remote or local LLM provider;
- datasource connections managed by administrators;
- automatic warehouse metadata discovery;
- editable semantic documentation;
- business-context definitions for metrics/events/terms;
- natural language -> structured cohort specification;
- deterministic SQL compilation;
- preview and execution;
- saved cohorts;
- cohort refresh;
- CSV export;
- A/A and A/B experiment setup;
- separate control/treatment exports;
- auditability and reproducibility.

## Saved cohort invariants

A saved cohort should preserve enough context to explain and reproduce how it was generated.

Persist at least:

- cohort id;
- name and description;
- original natural-language request;
- structured cohort definition;
- generated SQL or reproducible compiled representation;
- datasource id;
- semantic-context version;
- creator;
- creation time;
- relevant policy-context identifiers where required for audit.

Never store plaintext credentials or secrets in cohort definitions.

## Reproducibility

A saved cohort is not merely a CSV snapshot. The platform should be able to explain the definition that produced it and, where allowed, refresh it against current data.

If semantic definitions change, do not silently pretend an old cohort was produced under the new definitions. Preserve semantic/version provenance.

## Export

Exports are permission-controlled backend operations.

The platform should support user IDs as the minimum export and may expose additional authorized columns. Column/PII permissions apply to exports independently from query visibility.

## Product behavior principles

- show how the system interpreted the user's cohort request before costly execution when practical;
- favor explicit cohort definitions over opaque LLM behavior;
- preserve original natural-language intent for audit/debugging;
- make errors actionable;
- never use frontend-only checks as security controls;
- local-model support is a first-class requirement;
- MCP must not be required for the core product.

## Non-goal by default

Do not turn the product into a general-purpose BI assistant, dashboard builder, autonomous write-capable database agent, or marketing-delivery platform unless an approved product spec explicitly adds those capabilities.
EOF

write_skill "warehouse-semantic-layer" <<'EOF'
---
name: warehouse-semantic-layer
description: Rules for datasource adapters, metadata crawling, schema discovery, relationship discovery, semantic documentation, and business context. Use when implementing or changing DWH integrations or the semantic layer.
---

# Warehouse Semantic Layer

## Goal

Make the core application warehouse-agnostic while allowing an MVP to support PostgreSQL first.

## Adapter boundary

Do not scatter warehouse-specific behavior through domain code. Define a stable adapter boundary conceptually similar to:

```python
class WarehouseAdapter:
    def test_connection(...): ...
    def list_schemas(...): ...
    def list_tables(...): ...
    def get_columns(...): ...
    def get_relationships(...): ...
    def get_sample_values(...): ...
    def explain_query(...): ...
    def execute_readonly(...): ...
```

Exact signatures should follow the current codebase, but responsibilities must remain explicit.

## Metadata crawler

The crawler should discover, where supported:

- schemas;
- tables/views;
- columns;
- data types;
- primary keys;
- foreign keys / relationships;
- useful comments/descriptions;
- optional safe sample/distinct values;
- optional estimated row counts.

Never send raw secrets to an LLM.

Sampling must respect configured data-access and sensitive-data policy.

## Generated semantic documentation

Crawler output should produce editable boilerplate rather than pretending inferred semantics are authoritative.

Suggested logical documents:

```text
warehouse.md
entities.md
events.md
metrics.md
relationships.md
business-context.md
```

The exact storage format may evolve. The important property is that automatically inferred metadata and human-authored business meaning remain distinguishable and reviewable.

## Business context

Schema metadata is insufficient for correct cohort interpretation.

Business context should capture rules such as:

- which order statuses count as a purchase;
- how conversion is defined;
- event meaning;
- time windows;
- exclusions;
- canonical entity identifiers;
- terminology/synonyms.

Human-reviewed context is authoritative over LLM guesses.

## Versioning

Changes to semantic/business definitions should produce a version or immutable provenance reference that can be stored with saved cohorts.

## Portability

Separate metadata discovery from SQL dialect concerns. A new warehouse should normally require a new adapter/dialect implementation rather than edits throughout the domain layer.

## Safety

Connection testing and metadata discovery must be read-only where possible. Apply query timeouts and bounded sampling. Never log passwords, tokens, or full connection strings containing secrets.
EOF

write_skill "cohort-compiler" <<'EOF'
---
name: cohort-compiler
description: Rules for natural-language cohort interpretation, structured cohort specifications, deterministic SQL compilation, SQL validation, LLM provider abstraction, and safe read-only execution. Use when touching NL-to-cohort or SQL generation.
---

# Cohort Compiler

## Core principle

Do not make arbitrary LLM-generated SQL the trusted execution boundary.

Preferred flow:

```text
Natural Language
    -> LLM interpretation
    -> Structured Cohort Specification
    -> Semantic resolution
    -> Deterministic SQL compiler
    -> Access-policy enforcement
    -> SQL validation
    -> Read-only execution
```

## Structured cohort specification

The LLM should output a typed, validated domain structure representing intent rather than executable SQL.

Example conceptually:

```json
{
  "entity": "user",
  "conditions": {
    "operator": "OR",
    "conditions": [
      {"event": "product_liked", "product_id": "12345"},
      {"attribute": "referral_code", "operator": "=", "value": "GET100"}
    ]
  },
  "exclusions": [
    {"event": "purchase", "within_days": 7}
  ]
}
```

Exact schema belongs in code and must be versioned/tested.

## LLM provider abstraction

Core domain code must not depend directly on one vendor SDK.

Support an interface that can accommodate:

- OpenAI-compatible APIs;
- other cloud providers;
- locally hosted models.

Provider/model selection is configuration, not hardcoded business logic.

## Deterministic compiler

Once a cohort specification has been validated, SQL generation should be deterministic for the same semantic version, datasource/dialect, and spec.

Prefer an AST or structured query-building layer over fragile string concatenation.

## SQL safety

Warehouse execution is read-only. Reject mutating or administrative SQL, including:

- INSERT;
- UPDATE;
- DELETE;
- DROP;
- ALTER;
- TRUNCATE;
- CREATE;
- GRANT;
- REVOKE.

Where practical validate parsed SQL/AST, not only keywords.

Apply:

- query timeout;
- result-size limits;
- allowed datasource/schema/table policy;
- column restrictions;
- RLS after compilation and before execution.

## Policy boundary

The LLM must never enforce RBAC, RLS, PII restrictions, or datasource access. Those are deterministic backend responsibilities.

## Ambiguity

If a business request cannot be resolved safely from current semantic context, return an explicit clarification/error state. Do not silently invent a table, metric, event, or business definition.

## Tests

At minimum cover:

- natural language -> expected structured specification;
- schema validation failures;
- deterministic compilation;
- nested AND/OR conditions;
- exclusions/time windows;
- unknown semantic terms;
- SQL mutation rejection;
- dialect-specific behavior;
- policy application interactions.
EOF

write_skill "access-control" <<'EOF'
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
EOF

write_skill "experimentation" <<'EOF'
---
name: experimentation
description: Experimentation rules for A/A and A/B design, sample size, MDE, power, significance, deterministic user assignment, and cohort export. Use whenever implementing experiment calculations or splitting cohorts.
---

# Experimentation

## Scope

For an authorized cohort, users should be able to:

- configure A/A or A/B experiments;
- set/derive baseline conversion;
- configure MDE;
- configure alpha/significance;
- configure statistical power;
- calculate required sample size;
- choose group allocation;
- create deterministic assignments;
- export control/treatment cohorts separately.

## Statistical engine

Do not reimplement established statistical formulas without an explicit, documented reason.

Primary library/repository:

```text
Python package: statsmodels
GitHub: https://github.com/statsmodels/statsmodels
```

Wrap statsmodels behind an internal application interface. Domain/UI code should not call statsmodels directly everywhere.

Conceptually:

```python
class ExperimentCalculator:
    def required_sample_size(...): ...
    def minimum_detectable_effect(...): ...
    def analyze_binary_metric(...): ...
    def confidence_interval(...): ...
```

Useful statsmodels areas include `statsmodels.stats.power` and `statsmodels.stats.proportion`. Verify the exact API against the pinned installed version before implementation.

Reference project for workflow/API ideas only:

```text
https://github.com/brightertiger/abverdict
```

Do not make it a core dependency by default. Check licenses before copying any external source code.

## Separation of responsibilities

```text
Statistics            -> statsmodels wrapper
Experiment config     -> our domain logic
User assignment       -> our deterministic assignment engine
Cohort extraction     -> our DWH layer
Exports               -> our export layer
RBAC/RLS              -> our access-control layer
```

## Deterministic assignment

Do not use non-reproducible per-run randomness such as `random.random()` for persisted experiments.

Assignment must be stable for the same experiment and user, e.g. conceptually:

```text
stable_hash(experiment_id + canonical_user_id) -> bucket
```

Use an explicitly defined stable hash algorithm; do not depend on language-runtime hash functions that may be salted or version-dependent.

## Sample-size behavior

If the selected cohort is smaller than required sample size, do not silently proceed as if the experiment is adequately powered. Surface the constraint and require an explicit product-defined action.

## A/A

A/A splits use the same deterministic assignment machinery. Where the product supports diagnostics, allow balance checks on approved pre-treatment covariates without leaking restricted data.

## Tests

Test:

- known statsmodels-backed outputs;
- invalid parameter ranges;
- deterministic repeat assignment;
- no overlap between groups;
- allocation proportions within expected tolerance for large synthetic populations;
- stability after process restart;
- insufficient cohort size;
- permission enforcement around experiment creation/export.
EOF

# Create docs placeholders only if they do not already exist.
for f in "$DOCS_DIR/plans/README.md" "$DOCS_DIR/product/README.md" "$DOCS_DIR/decisions/README.md"; do
  if [[ ! -e "$f" ]]; then
    case "$f" in
      */plans/*) printf '# Implementation Plans\n\nOne major feature/branch per plan.\n' > "$f" ;;
      */product/*) printf '# Product Specifications\n\nDecision-grade feature specifications produced before implementation.\n' > "$f" ;;
      */decisions/*) printf '# Architecture Decisions\n\nRecord consequential architectural decisions and tradeoffs here.\n' > "$f" ;;
    esac
  fi
done

say "Adding safe local-secret patterns to .gitignore"
GITIGNORE="$ROOT/.gitignore"
touch "$GITIGNORE"
if ! grep -q "COHORT_PLATFORM_LOCAL_SECRETS" "$GITIGNORE"; then
  cat >> "$GITIGNORE" <<'EOF'

# COHORT_PLATFORM_LOCAL_SECRETS
.env
.env.*
!.env.example
.secrets/
*.pem
*.key
# END_COHORT_PLATFORM_LOCAL_SECRETS
EOF
fi

say "Fetching upstream engineering skills"
SUPERPOWERS_REPO="${SUPERPOWERS_REPO:-https://github.com/obra/superpowers.git}"
ANTHROPIC_REPO="${ANTHROPIC_REPO:-https://github.com/anthropics/claude-code.git}"
VERCEL_REPO="${VERCEL_REPO:-https://github.com/vercel-labs/agent-skills.git}"
WSHOBSON_REPO="${WSHOBSON_REPO:-https://github.com/wshobson/agents.git}"

clone_upstream() {
  local url="$1"
  local dest="$2"
  git clone --depth 1 --quiet "$url" "$dest" || fail "Failed to clone $url"
}

clone_upstream "$SUPERPOWERS_REPO" "$TMP_DIR/superpowers"
clone_upstream "$ANTHROPIC_REPO" "$TMP_DIR/anthropic-claude-code"
clone_upstream "$VERCEL_REPO" "$TMP_DIR/vercel-agent-skills"
clone_upstream "$WSHOBSON_REPO" "$TMP_DIR/wshobson-agents"

SUPERPOWERS_SHA="$(git -C "$TMP_DIR/superpowers" rev-parse HEAD)"
ANTHROPIC_SHA="$(git -C "$TMP_DIR/anthropic-claude-code" rev-parse HEAD)"
VERCEL_SHA="$(git -C "$TMP_DIR/vercel-agent-skills" rev-parse HEAD)"
WSHOBSON_SHA="$(git -C "$TMP_DIR/wshobson-agents" rev-parse HEAD)"

vendor_skill() {
  local source="$1"
  local target_name="$2"
  local target="$SKILLS_DIR/$target_name"

  [[ -f "$source/SKILL.md" ]] || fail "Expected skill not found: $source/SKILL.md"

  if [[ -e "$target" ]] && ! grep -q "$BOOTSTRAP_MARKER" "$target/SKILL.md" 2>/dev/null; then
    # External vendored skills intentionally get replaced on bootstrap/update.
    # The lock file records the exact upstream source commit.
    rm -rf "$target"
  else
    rm -rf "$target"
  fi
  cp -a "$source" "$target"
}

# Superpowers workflow skills.
for skill in \
  writing-plans \
  test-driven-development \
  systematic-debugging \
  verification-before-completion \
  requesting-code-review \
  using-git-worktrees \
  executing-plans \
  finishing-a-development-branch; do
  vendor_skill "$TMP_DIR/superpowers/skills/$skill" "$skill"
done

# Frontend/UI skills from high-signal upstream repositories.
vendor_skill "$TMP_DIR/anthropic-claude-code/plugins/frontend-design/skills/frontend-design" "frontend-design"
vendor_skill "$TMP_DIR/vercel-agent-skills/skills/react-best-practices" "vercel-react-best-practices"
vendor_skill "$TMP_DIR/vercel-agent-skills/skills/composition-patterns" "vercel-composition-patterns"

# wshobson backend/data engineering skills.
vendor_skill "$TMP_DIR/wshobson-agents/plugins/backend-development/skills/api-design-principles" "api-design-principles"
vendor_skill "$TMP_DIR/wshobson-agents/plugins/api-scaffolding/skills/fastapi-templates" "fastapi-templates"
vendor_skill "$TMP_DIR/wshobson-agents/plugins/python-development/skills/python-testing-patterns" "python-testing-patterns"
vendor_skill "$TMP_DIR/wshobson-agents/plugins/developer-essentials/skills/auth-implementation-patterns" "auth-implementation-patterns"
vendor_skill "$TMP_DIR/wshobson-agents/plugins/database-design/skills/postgresql-table-design" "postgresql-table-design"
vendor_skill "$TMP_DIR/wshobson-agents/plugins/developer-essentials/skills/error-handling-patterns" "error-handling-patterns"

say "Preserving upstream licenses"
copy_license() {
  local repo_dir="$1"
  local prefix="$2"
  local found=""
  for candidate in LICENSE LICENSE.md LICENSE.txt COPYING; do
    if [[ -f "$repo_dir/$candidate" ]]; then
      cp "$repo_dir/$candidate" "$LICENSE_DIR/${prefix}-${candidate//\//_}"
      found="yes"
      break
    fi
  done
  [[ -n "$found" ]] || warn "No root license file found for $prefix; inspect upstream before redistributing."
}
copy_license "$TMP_DIR/superpowers" "obra-superpowers"
copy_license "$TMP_DIR/anthropic-claude-code" "anthropic-claude-code"
copy_license "$TMP_DIR/vercel-agent-skills" "vercel-agent-skills"
copy_license "$TMP_DIR/wshobson-agents" "wshobson-agents"

say "Writing upstream lock file"
cat > "$AGENTS_DIR/skills-lock.md" <<EOF
<!-- $BOOTSTRAP_MARKER -->
# Vendored Agent Skills Lock

Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)

Vendored external skills are copied into this repository for reproducibility. Re-run \`scripts/bootstrap-agents.sh\` intentionally to refresh them.

| Source | Repository | Commit |
|---|---|---|
| Superpowers | $SUPERPOWERS_REPO | \`$SUPERPOWERS_SHA\` |
| Anthropic Claude Code | $ANTHROPIC_REPO | \`$ANTHROPIC_SHA\` |
| Vercel Agent Skills | $VERCEL_REPO | \`$VERCEL_SHA\` |
| wshobson/agents | $WSHOBSON_REPO | \`$WSHOBSON_SHA\` |

## Selected skills

### Project-specific
- grill-me
- cohort-platform-domain
- warehouse-semantic-layer
- cohort-compiler
- access-control
- experimentation

### Superpowers
- writing-plans
- test-driven-development
- systematic-debugging
- verification-before-completion
- requesting-code-review
- using-git-worktrees
- executing-plans
- finishing-a-development-branch

### Frontend
- frontend-design (Anthropic)
- vercel-react-best-practices (Vercel)
- vercel-composition-patterns (Vercel)

### wshobson
- api-design-principles
- fastapi-templates
- python-testing-patterns
- auth-implementation-patterns
- postgresql-table-design
- error-handling-patterns

See \`.agents/vendor-licenses/\` for copied upstream license notices.
EOF

say "Validating skill installation"
EXPECTED_SKILLS=(
  grill-me
  cohort-platform-domain
  warehouse-semantic-layer
  cohort-compiler
  access-control
  experimentation
  writing-plans
  test-driven-development
  systematic-debugging
  verification-before-completion
  requesting-code-review
  using-git-worktrees
  executing-plans
  finishing-a-development-branch
  frontend-design
  vercel-react-best-practices
  vercel-composition-patterns
  api-design-principles
  fastapi-templates
  python-testing-patterns
  auth-implementation-patterns
  postgresql-table-design
  error-handling-patterns
)

FAILED=0
for skill in "${EXPECTED_SKILLS[@]}"; do
  file="$SKILLS_DIR/$skill/SKILL.md"
  if [[ ! -f "$file" ]]; then
    printf '  [MISSING] %s\n' "$skill" >&2
    FAILED=1
    continue
  fi
  if ! grep -Eq '^name:[[:space:]]*' "$file"; then
    printf '  [INVALID frontmatter: name] %s\n' "$skill" >&2
    FAILED=1
  fi
  if ! grep -Eq '^description:[[:space:]]*' "$file"; then
    printf '  [INVALID frontmatter: description] %s\n' "$skill" >&2
    FAILED=1
  fi
  printf '  [OK] %s\n' "$skill"
done

[[ "$FAILED" -eq 0 ]] || fail "One or more skills failed validation."

say "Bootstrap complete"
printf '%s\n' \
  "AGENTS.md:               $ROOT/AGENTS.md" \
  "Skills:                  $SKILLS_DIR" \
  "Skill source lock:       $AGENTS_DIR/skills-lock.md" \
  "Vendor licenses:         $LICENSE_DIR" \
  "Feature plans:           $DOCS_DIR/plans" \
  "Product specs:           $DOCS_DIR/product" \
  "Architecture decisions:  $DOCS_DIR/decisions"

printf '\nSuggested first prompt to your coding agent:\n\n'
printf '  Use grill-me to define the MVP architecture and first implementation feature. Do not write code until the product spec and feature plan are complete.\n'

printf '\nGit status:\n'
git status --short || true
