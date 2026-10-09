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
