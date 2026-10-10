"""The LLM's tagged result and the bounded retry loop (FR-4).

The LLM answers with exactly one of:

* ``{"kind": "interpretation", "spec": <cohort spec v1>}``
* ``{"kind": "clarification", "question": "..."}``
* ``{"kind": "rejection", "reason": "mutation"|"analytics"|"unknown_term"|
  "unsupported_construct", "message": "..."}``

Anything else is a schema violation: the validation error is fed back and the request
retried, at most ``max_attempts`` times in total, then :class:`LlmInvalidOutputError`.
The raw output of every attempt is logged for debugging, scrubbed of anything that looks
like a credential.
"""

import json
import logging
import re
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from cohortsplit.cohort_spec.v1 import CohortSpecV1
from cohortsplit.llm.client import ChatClient, ChatMessage, LlmError

logger = logging.getLogger(__name__)

MAX_LOGGED_OUTPUT = 4000
RejectionReason = Literal["mutation", "analytics", "unknown_term", "unsupported_construct"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_SECRETS = (
    (re.compile(r"(\w+://)[^/\s:@]*:[^@\s]*@"), r"\1***@"),  # user:password@ in URLs
    (re.compile(r"(?i)\b(bearer\s+)\S+"), r"\1***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{6,}"), "sk-***"),
    (re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)(\s*[=:]\s*)\S+"), r"\1\2***"),
)


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Interpretation(_Result):
    kind: Literal["interpretation"] = "interpretation"
    spec: CohortSpecV1


class Clarification(_Result):
    kind: Literal["clarification"] = "clarification"
    question: Text


class Rejection(_Result):
    kind: Literal["rejection"] = "rejection"
    reason: RejectionReason
    message: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


LlmResult = Interpretation | Clarification | Rejection
_RESULT: TypeAdapter[LlmResult] = TypeAdapter(Annotated[LlmResult, Field(discriminator="kind")])


class LlmInvalidOutputError(LlmError):
    def __init__(self, attempts: int) -> None:
        super().__init__(
            f"The LLM returned an invalid cohort specification {attempts} times in a row. "
            "Rephrase the request or check the configured model."
        )
        self.attempts = attempts


def scrub(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def _parse(raw: str) -> LlmResult:
    fenced = _FENCE.match(raw)
    text = fenced.group(1) if fenced else raw
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"the answer is not valid JSON ({exc.msg})") from None
    return _RESULT.validate_python(value)


def _feedback(error: Exception) -> str:
    if isinstance(error, ValidationError):
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in error.errors(include_input=False, include_url=False)[:10]
        )
    else:
        details = str(error)
    return (
        f"Your previous answer was invalid: {details}. Answer again with exactly one JSON "
        "object that follows the contract (interpretation, clarification or rejection)."
    )


def interpret_request(
    client: ChatClient, messages: Sequence[ChatMessage], *, max_attempts: int
) -> LlmResult:
    conversation = list(messages)
    for attempt in range(1, max_attempts + 1):
        raw = client.complete(conversation)
        try:
            result = _parse(raw)
        except (ValueError, ValidationError) as exc:
            logger.warning(
                "llm output invalid attempt=%d/%d raw=%s",
                attempt,
                max_attempts,
                scrub(raw[:MAX_LOGGED_OUTPUT]),
            )
            conversation += [ChatMessage("assistant", raw), ChatMessage("user", _feedback(exc))]
            continue
        logger.debug(
            "llm output attempt=%d kind=%s raw=%s",
            attempt,
            result.kind,
            scrub(raw[:MAX_LOGGED_OUTPUT]),
        )
        return result
    raise LlmInvalidOutputError(max_attempts)
