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
