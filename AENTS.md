# AGENTS.md

## Project

This repository contains a self-hosted customer cohort and experimentation platform.

A user should be able to clone the repository, configure a supported data warehouse and LLM provider, start the application, and create reusable customer audiences from natural-language business intent.

Example:

> Users who liked product `12345` or joined through referral campaign `GET100`.

This is **not a generic NL-to-SQL chatbot**.

The core flow is:

```text
Natural Language
    ↓
Structured Cohort Specification
    ↓
Deterministic SQL Compiler
    ↓
Authorization / Data Policies
    ↓
SQL Validation
    ↓
DWH
    ↓
Cohort
    ↓
Save / Export / A-A / A-B
```

The platform must support local LLMs as well as remote providers and must not require MCP.

---

# Skill-First Development

Detailed instructions are intentionally kept out of this file.

Before performing work, determine which skills apply and read them.

Project-specific skills:

```text
grill-me
cohort-platform-domain
warehouse-semantic-layer
cohort-compiler
access-control
experimentation
```

External engineering skills should be used for general software-development expertise rather than duplicating their guidance here.

---

# Skill Routing

## Requirements are unclear

Use:

```text
grill-me
```

Use it before implementing a substantial feature when product behavior, UX, edge cases, security expectations, or acceptance criteria are insufficiently defined.

Do not guess important product decisions.

---

## General product/domain behavior

Use:

```text
cohort-platform-domain
```

This is the canonical source for:

- product purpose;
- cohort lifecycle;
- saved cohorts;
- exports;
- users and administrators;
- product invariants;
- terminology.

---

## Warehouse connection, metadata or semantics

Use:

```text
warehouse-semantic-layer
```

This owns:

- DWH adapters;
- metadata crawling;
- schemas/tables/columns;
- relationship discovery;
- generated semantic documentation;
- business-context documentation;
- warehouse abstraction.

---

## Natural language or SQL generation

Use:

```text
cohort-compiler
```

This owns:

- natural-language interpretation;
- structured cohort specifications;
- semantic resolution;
- deterministic SQL compilation;
- SQL validation;
- read-only execution boundaries;
- LLM provider abstraction.

The LLM must never directly execute arbitrary SQL.

---

## Authentication, permissions or protected data

Use:

```text
access-control
```

This owns:

- authentication;
- platform administration;
- dynamic RBAC;
- datasource authorization;
- RLS;
- column-level security;
- PII;
- credentials and secrets;
- audit logging.

Security-sensitive behavior must fail closed.

---

## Experiments

Use:

```text
experimentation
```

This owns:

- A/A experiments;
- A/B experiments;
- power calculations;
- required sample size;
- MDE;
- significance configuration;
- deterministic group assignment;
- experiment export.

Statistical calculations must use the project's approved statistical library rather than custom formulas.

---

# External Engineering Skills

Use the installed upstream skills rather than reproducing their instructions here.

## Development Workflow

Source:

```text
obra/superpowers
```

Required skills where relevant:

```text
writing-plans
test-driven-development
systematic-debugging
verification-before-completion
requesting-code-review
finishing-a-development-branch
```

Development must follow their planning, RED-GREEN-REFACTOR, debugging and verification practices.

---

## Frontend

Source:

```text
vercel-labs/agent-skills
```

Use:

```text
react-best-practices
composition-patterns
web-design-guidelines
```

Use them when:

- creating React pages/components;
- designing frontend architecture;
- implementing state/data fetching;
- reviewing frontend performance;
- building reusable UI components;
- performing UX/accessibility review.

Do not invent project-specific frontend conventions when an established project convention already exists.

---

## Backend

Source:

```text
wshobson/agents
```

Use where relevant:

```text
api-design-principles
architecture-patterns
fastapi-templates
python-testing-patterns
auth-implementation-patterns
postgresql-table-design
sql-optimization-patterns
error-handling-patterns
```

Use them for API design, Python/FastAPI development, authentication, persistence, testing, database design and backend architecture.

---

# Mandatory Feature Workflow

Every significant feature must use its own feature branch.

Examples:

```text
feature/authentication
feature/rbac
feature/datasource-management
feature/warehouse-crawler
feature/cohort-compiler
feature/experimentation-engine
```

Do not implement unrelated major features in the same branch.

Before implementation create:

```text
docs/plans/<feature-name>.md
```

The plan must define:

- problem;
- desired behavior;
- scope;
- non-goals;
- interfaces;
- data-model changes;
- implementation steps;
- tests;
- edge cases;
- security implications;
- acceptance criteria;
- definition of done.

If important product requirements are unclear, invoke `grill-me` before finalizing the plan.

---

# Test-First Rule

Production code for new behavior must not be written before its expected behavior is represented by tests.

Follow:

```text
RED
→ failing test

GREEN
→ minimum implementation

REFACTOR
→ improve structure while preserving behavior
```

Never modify or weaken a valid test merely to make implementation pass.

Security-sensitive features require explicit negative tests.

Examples:

```text
authorized user can export cohort
unauthorized user cannot export cohort

KZ-scoped user can retrieve KZ users
KZ-scoped user cannot retrieve UAE users

user with pii.export can export email
user without pii.export cannot export email
```

---

# Core Architectural Boundaries

Keep these concepts independent:

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

Do not merge these responsibilities for convenience.

Domain code should not depend directly on framework or UI concerns.

---

# LLM Boundary

The LLM is an interpreter, not the security or execution authority.

Preferred flow:

```text
Natural language
    ↓
LLM
    ↓
Structured cohort specification
    ↓
Deterministic application code
    ↓
SQL
```

Do not use:

```text
Natural language
    ↓
LLM
    ↓
arbitrary executable SQL
```

LLMs must not be responsible for:

- RLS;
- permissions;
- security filtering;
- secret handling;
- experiment assignment;
- destructive database operations.

Support provider abstraction so that cloud and local models can be used.

---

# Database Safety

Generated warehouse queries must be read-only.

Reject mutation or administration statements including:

```text
INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
CREATE
GRANT
REVOKE
```

Enforce where applicable:

- query timeout;
- result limits;
- schema/table allowlists;
- column restrictions;
- AST validation;
- datasource authorization;
- RLS.

Never expose warehouse credentials to the LLM.

---

# Code Quality

Prefer:

```text
explicit > clever
small cohesive modules > large generic abstractions
composition > unnecessary inheritance
deterministic behavior > hidden randomness
existing abstraction > duplicate implementation
```

Do not:

- hardcode roles;
- hardcode customer schemas;
- hardcode DWH table names;
- hardcode model providers;
- embed secrets in source code;
- create abstractions without a concrete use case;
- silently catch failures;
- perform unrelated refactoring inside feature branches.

Use structured logging.

Use typed interfaces and models where practical.

Keep domain logic independently testable.

---

# Completion Gate

Before declaring work complete:

1. Run relevant unit tests.
2. Run integration tests where applicable.
3. Run security/authorization tests where applicable.
4. Run linting and type checks.
5. Verify database migrations.
6. Verify the acceptance criteria from the feature plan.
7. Use `verification-before-completion`.
8. Perform code review.
9. Update affected documentation.

Do not claim completion from code inspection alone.

Provide evidence that the feature works.

---

# Source of Truth

When instructions conflict, use this priority:

```text
1. Explicit current user requirement
2. Approved feature plan
3. Project-specific skill
4. AGENTS.md
5. External engineering skill
6. Existing implementation convention
```

Security constraints may not be silently weakened by a lower-priority instruction.