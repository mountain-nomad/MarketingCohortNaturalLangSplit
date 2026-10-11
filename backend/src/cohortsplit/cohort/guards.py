"""Deterministic pre-LLM guards (BR-3, BR-4, BR-6, ruling C7).

Applied to the request text **before** the LLM is called, so these rejections never
depend on the model's behaviour. They are deliberately narrow (imperative mutation verbs,
SQL-like DML/DDL, question openers, ordering words); the LLM's own rejection, the spec
model (no sequences, no SQL) and the SQL validator remain independent backstops.
"""

import re

from cohortsplit.llm.interpreter import Rejection

MUTATION_MESSAGE = (
    "This looks like a request to change data. CohortSplit only builds read-only audiences; "
    "nothing was changed. Describe the users you want instead, e.g. 'Users who …'."
)
ANALYTICS_MESSAGE = (
    "This looks like an analytics question, not a cohort definition. Describe the users "
    "you want, e.g. 'Users who placed an order in the last 30 days'."
)
SEQUENCE_MESSAGE = (
    'Ordered sequences of events ("did A, then B", "within N days after A") are not '
    "supported yet. Describe the audience without an order of events."
)

_POLITE = re.compile(
    r"^(?:(?:please|kindly|now|so|ok|okay|hey)\b[\s,]*"
    r"|(?:can|could|would|will) you\b[\s,]*"
    r"|(?:i|we) (?:want|need|would like|'d like) to\b[\s,]*"
    r"|i'd like to\b[\s,]*|go ahead and\b[\s,]*|let'?s\b[\s,]*|you should\b[\s,]*)+",
    re.IGNORECASE,
)
MUTATION_VERBS = frozenset(
    {
        "add", "alter", "archive", "assign", "ban", "block", "cancel", "change", "clear",
        "copy", "create", "deactivate", "delete", "destroy", "disable", "drop", "edit",
        "erase", "grant", "import", "insert", "kill", "load", "mark", "merge", "modify",
        "move", "overwrite", "purge", "remove", "rename", "replace", "reset", "restore",
        "revoke", "set", "suspend", "truncate", "unsubscribe", "update", "upsert", "wipe",
        "write",
    }
)  # fmt: skip
_SQL_WRITE = re.compile(
    r"\b(?:delete\s+from|insert\s+into|update\s+\S+\s+set|merge\s+into"
    r"|drop\s+(?:table|schema|database|view|index|role|user|function)"
    r"|truncate(?:\s+table)?\s+\w+|alter\s+(?:table|role|user|schema|database|view)"
    r"|grant\s+\w+\s+(?:on|to)\b|revoke\s+\w+\s+(?:on|from)\b"
    r"|create\s+(?:table|role|user|index|view|schema|database|function))\b",
    re.IGNORECASE,
)
ANALYTICS_OPENERS = frozenset(
    {
        "what", "what's", "whats", "how", "why", "when", "where", "compare", "calculate",
        "compute", "plot", "chart", "graph", "forecast", "predict",
    }
)  # fmt: skip
# Openers that are only a question with a question mark ("Has a paid order" is a cohort).
QUESTION_OPENERS = frozenset(
    {
        "is", "are", "was", "were", "does", "do", "did", "has", "have", "will", "would",
        "should", "average", "sum", "total",
    }
)  # fmt: skip
# Building an audience is not a data change: "Create a cohort of …", "Add users … to a list".
AUDIENCE_VERBS = frozenset({"add", "build", "create", "make"})
_AUDIENCE = re.compile(r"\b(?:cohorts?|segments?|audiences?|lists?)\b", re.IGNORECASE)
_SEQUENCE = re.compile(
    r"\bthen\b|\bfollowed by\b|\bafterwards\b|\bsubsequently\b"
    r"|\b(?:within|in)\s+\d+\s*(?:minutes?|mins?|hours?|h|days?|d|weeks?|months?)\s+"
    r"(?:of|after|before)\b"
    r"|\bafter\s+(?:adding|viewing|visiting|receiving|opening|signing|registering|buying|"
    r"purchasing|placing|abandoning|joining|creating|installing|clicking)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z']+")


def _first_word(text: str) -> str:
    stripped = _POLITE.sub("", text.strip())
    match = _WORD.match(stripped.lower())
    return match.group(0) if match else ""


def pre_guard(nl_request: str) -> Rejection | None:
    """A deterministic rejection for mutation / analytics / sequence requests, else None."""
    first = _first_word(nl_request)
    audience = first in AUDIENCE_VERBS and bool(_AUDIENCE.search(nl_request))
    if (first in MUTATION_VERBS and not audience) or _SQL_WRITE.search(nl_request):
        return Rejection(reason="mutation", message=MUTATION_MESSAGE)
    question = nl_request.rstrip().endswith("?")
    if first in ANALYTICS_OPENERS or (first in QUESTION_OPENERS and question):
        return Rejection(reason="analytics", message=ANALYTICS_MESSAGE)
    if _SEQUENCE.search(nl_request):
        return Rejection(reason="unsupported_construct", message=SEQUENCE_MESSAGE)
    return None
