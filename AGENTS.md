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
