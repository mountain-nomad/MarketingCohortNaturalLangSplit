"""Business-context entries (FR-3). STUB: implemented in the GREEN commit."""

from typing import Any

from pydantic import BaseModel, ConfigDict

ENTRY_KINDS = (
    "term",
    "metric",
    "status_semantics",
    "time_window",
    "exclusion",
    "canonical_user_id",
)


class BusinessContextIn(BaseModel):
    model_config = ConfigDict(extra="allow")


Definition = Any


def definition_references(definition: Definition) -> tuple[frozenset[str], frozenset[str]]:
    raise NotImplementedError
