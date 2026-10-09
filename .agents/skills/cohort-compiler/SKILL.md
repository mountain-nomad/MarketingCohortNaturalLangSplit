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
