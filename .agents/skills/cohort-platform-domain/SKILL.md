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
