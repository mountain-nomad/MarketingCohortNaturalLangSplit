"""The interpretation prompt, built **only** from one semantic snapshot and the request.

BR-7 / AC-24 / AC-28: the snapshot already contains nothing but confirmed use cases,
reviewed business context, raw schema metadata and policy-permitted samples. This module
takes no other input, so warehouse credentials, cohort result rows and pending use cases
cannot reach the LLM.
"""

import json
from typing import Any

from cohortsplit.cohort_spec.upgrade import try_load_spec
from cohortsplit.cohort_spec.v1 import spec_document
from cohortsplit.llm.client import ChatMessage
from cohortsplit.semantic.snapshot import PromptTable, SemanticContextSnapshot

INSTRUCTIONS = """\
You translate a marketer's request into a cohort specification for CohortSplit.
A cohort is always a set of users, identified by the canonical user id {canonical}.

Answer with exactly ONE JSON object and nothing else. It is one of:
{{"kind": "interpretation", "spec": <cohort spec v1>}}
{{"kind": "clarification", "question": "<one question for the marketer>"}}
{{"kind": "rejection", "reason": "mutation" | "analytics" | "unknown_term" | \
"unsupported_construct", "message": "<short explanation>"}}

Rules:
- Use ONLY the tables, columns, relationships, values and business terms in the context
  below. Never invent a table, column, relationship, value or definition.
- If the request uses a business word that is neither defined in business_context nor
  readable directly from a column, answer with a clarification that names the word.
- Requests to change, delete or create data: rejection "mutation".
- Questions about numbers or metrics instead of a list of users: rejection "analytics".
- Ordered sequences ("did A, then B", "within N days after A") are not supported yet:
  rejection "unsupported_construct". Never drop part of a request silently.
- Never write SQL.

Cohort spec v1:
{{"spec_version": "1", "entity": "user", "where": <condition>}}  (omit "where" for all users)
Conditions, selected by "type":
- {{"type": "attribute", "filter": <filter>}}: a column of the user table.
- {{"type": "attribute_time_window", "window": <window>}}: a date column of the user table.
- {{"type": "related", "table": "<schema.table>", "path": [<hop>, ...], "filters": [<filter>, \
...], "time_window": <window>, "aggregate": <aggregate>}}: rows of another table linked to
  the user. "path" lists foreign-key hops from the user table, each
  {{"from_column": "<schema.table.column>", "to_column": "<schema.table.column>"}}; omit it only
  when exactly one shortest relationship exists. Filters may use any table on the path.
- {{"type": "metric", "term": "<business_context metric>", "path": ..., "filters": ..., \
"time_window": ..., "aggregate": ...}}: the same over a defined business metric.
- {{"type": "all_of" | "any_of", "conditions": [<condition>, ...]}}, \
{{"type": "not", "condition": <condition>}}.
<filter>: {{"column": "<schema.table.column>", "operator": "=" | "!=" | ">" | ">=" | "<" | \
"<=" | "in" | "not_in" | "is_null" | "is_not_null", "value": <literal or list of literals>}}
<window>: {{"kind": "relative", "column": "<schema.table.column>", "last_days": <days>}} | \
{{"kind": "absolute", "column": "<schema.table.column>", "start": "YYYY-MM-DD", "end": \
"YYYY-MM-DD"}} (UTC, end exclusive) | {{"kind": "term", "term": "<business_context time window>"}}
<aggregate>: {{"function": "count", "operator": ">=", "value": 2}} | {{"function": "sum", \
"column": "<schema.table.column>", "operator": ">", "value": 500}} (over distinct rows of the \
condition's table)

Context (JSON):
{context}
"""


def _table(table: PromptTable) -> dict[str, Any]:
    name = table.qualified_name
    return {
        "table": name,
        "comment": table.comment,
        "estimated_rows": table.estimated_row_count,
        "primary_key": [f"{name}.{c}" for c in table.primary_key],
        "foreign_keys": [
            {
                "from": f"{name}.{column}",
                "to": f"{fk.referred_table}.{referred}",
            }
            for fk in table.foreign_keys
            if len(fk.columns) == 1
            for column, referred in zip(fk.columns, fk.referred_columns, strict=False)
        ],
        "columns": [
            {
                "column": f"{name}.{c.name}",
                "type": c.data_type,
                "nullable": c.nullable,
                **({"comment": c.comment} if c.comment else {}),
                **(
                    {"allowed_values": list(c.allowed_values)}
                    if c.allowed_values is not None
                    else {}
                ),
                **({"sample_values": list(c.sample_values)} if c.sample_values else {}),
            }
            for c in table.columns
        ],
    }


def _context(snapshot: SemanticContextSnapshot) -> str:
    context = {
        "canonical_user_id": snapshot.canonical_user_id,
        "business_context": [
            {
                "term": entry.key,
                "synonyms": list(entry.synonyms),
                "description": entry.description,
                "definition": dict(entry.definition),
            }
            for entry in snapshot.business_context
        ],
        "tables": [_table(t) for t in snapshot.tables],
    }
    return json.dumps(context, sort_keys=True, ensure_ascii=False)


def build_prompt(snapshot: SemanticContextSnapshot, nl_request: str) -> list[ChatMessage]:
    """System instructions + context, confirmed use cases as few-shot pairs, the request."""
    messages = [
        ChatMessage(
            "system",
            INSTRUCTIONS.format(canonical=snapshot.canonical_user_id, context=_context(snapshot)),
        )
    ]
    for use_case in snapshot.confirmed_use_cases:
        spec = try_load_spec(use_case.spec)
        if spec is None:
            continue  # not representable in v1: never shown
        messages.append(ChatMessage("user", use_case.nl_request))
        messages.append(
            ChatMessage(
                "assistant",
                json.dumps(
                    {"kind": "interpretation", "spec": spec_document(spec)},
                    sort_keys=True,
                    ensure_ascii=False,
                ),
            )
        )
    messages.append(ChatMessage("user", nl_request))
    return messages
