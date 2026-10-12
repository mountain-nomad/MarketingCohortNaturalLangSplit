"""The stored experiment run as an immutable value (no ORM, no I/O)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from cohortsplit.experiment.assignment import Allocation

ExperimentType = Literal["ab", "aa"]
UserIdType = Literal["integer", "string"]


@dataclass(frozen=True)
class RunRecord:
    """A split run: the definition it was made from, the assignment parameters and the
    per-group counts. The membership snapshot (canonical id + group) is stored separately
    and holds nothing else (AC-21c)."""

    id: str
    created_by: int
    created_at: datetime
    prepared_cohort_id: str | None
    nl_request: str
    spec: Mapping[str, Any]
    spec_hash: str
    sql: str
    sql_params: Mapping[str, Any]
    sql_hash: str
    dialect: str
    as_of: datetime
    semantic_version: str
    canonical_user_id: str
    user_id_type: UserIdType
    experiment_key: str
    experiment_type: ExperimentType
    allocation: Allocation
    assignment_version: int
    test_count: int
    control_count: int
    export_columns: tuple[str, ...]
    download_count: int

    @property
    def total(self) -> int:
        return self.test_count + self.control_count

    @property
    def empty_groups(self) -> tuple[str, ...]:
        return tuple(
            group
            for group, count in (("test", self.test_count), ("control", self.control_count))
            if count == 0
        )
