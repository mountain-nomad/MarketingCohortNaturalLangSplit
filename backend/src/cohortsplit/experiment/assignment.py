"""Deterministic test/control assignment (FR-8, BR-5, ruling E1).

Assignment version 1, the only one so far::

    message = UTF-8(experiment_key) || 0x00 || UTF-8(canonical_id_text(user_id))
    bucket  = unsigned big-endian integer of the first 8 bytes of SHA-256(message) mod 10,000
    group   = "test" if bucket < test_percent * 100 else "control"

The experiment key cannot contain NUL (key alphabet), so the separator is unambiguous.
The group depends only on the key, the user id and the allocation (BR-5): never on the
cohort size, row order, run time, process or Python's salted ``hash()``. The bias of
reducing a 64-bit integer modulo 10,000 is below 1e-15 per bucket.

Golden vectors in ``tests/unit/experiment/test_assignment.py`` pin the algorithm; any
change must introduce a new ``ASSIGNMENT_VERSION`` instead of altering this one.
"""

import hashlib
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ASSIGNMENT_VERSION = 1
BUCKETS = 10_000
_BYTES = 8
SEPARATOR = b"\x00"

Group = Literal["test", "control"]

# Filename-safe and unambiguous: ASCII letters/digits, inner "_", "-", "." (ruling E2).
KEY_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?$")
KEY_RULE = (
    "1-100 characters: ASCII letters, digits, '_', '-' or '.', starting and ending with a "
    "letter or digit"
)


class InvalidExperimentKeyError(ValueError):
    pass


def validate_experiment_key(raw: str) -> str:
    """The trimmed key, or :class:`InvalidExperimentKeyError`."""
    key = raw.strip()
    if not key:
        raise InvalidExperimentKeyError("The experiment key is required.")
    if not KEY_PATTERN.fullmatch(key):
        raise InvalidExperimentKeyError(f"The experiment key must be {KEY_RULE}.")
    return key


class Allocation(BaseModel):
    """Test/control shares in whole percent: each at least 1, summing to 100."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    test: int = Field(default=50, ge=1, le=99)
    control: int = Field(default=50, ge=1, le=99)

    @model_validator(mode="after")
    def _sums_to_100(self) -> "Allocation":
        if self.test + self.control != 100:
            raise ValueError("test and control must sum to 100")
        return self

    @property
    def threshold(self) -> int:
        """Buckets below this value are the test group."""
        return self.test * BUCKETS // 100


def canonical_id_text(user_id: object) -> str:
    """The text a user id is hashed as: decimal for integers, the value for strings,
    the canonical form for UUIDs. Anything else is refused (fail closed)."""
    if isinstance(user_id, bool):
        raise TypeError("boolean user ids are not supported")
    if isinstance(user_id, int):
        return str(user_id)
    if isinstance(user_id, str):
        if not user_id:
            raise ValueError("empty user id")
        return user_id
    if isinstance(user_id, uuid.UUID):
        return str(user_id)
    raise TypeError(f"unsupported user id type {type(user_id).__name__}")


def bucket(experiment_key: str, user_id_text: str) -> int:
    digest = hashlib.sha256(
        experiment_key.encode("utf-8") + SEPARATOR + user_id_text.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:_BYTES], "big") % BUCKETS


def assign(experiment_key: str, user_id: object, allocation: Allocation) -> Group:
    value = bucket(experiment_key, canonical_id_text(user_id))
    return "test" if value < allocation.threshold else "control"


@dataclass(frozen=True)
class Split:
    test: tuple[object, ...]
    control: tuple[object, ...]


def split(experiment_key: str, user_ids: Iterable[object], allocation: Allocation) -> Split:
    """Both groups, each in input order. Disjoint; their union is the input."""
    test: list[object] = []
    control: list[object] = []
    for user_id in user_ids:
        (test if assign(experiment_key, user_id, allocation) == "test" else control).append(user_id)
    return Split(tuple(test), tuple(control))
