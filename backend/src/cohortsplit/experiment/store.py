"""Run repository: create a run with its membership snapshot, visibility-filtered reads,
and ordered member batches for export (bounded memory)."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from cohortsplit.auth.models import User
from cohortsplit.experiment.assignment import Allocation
from cohortsplit.experiment.models import ExperimentRun, ExperimentRunMember
from cohortsplit.experiment.records import ExperimentType, RunRecord, UserIdType

INSERT_BATCH = 5_000


@dataclass(frozen=True)
class Creator:
    id: int
    email: str
    display_name: str


@dataclass(frozen=True)
class RunView:
    run: RunRecord
    creator: Creator


def record_of(row: ExperimentRun) -> RunRecord:
    user_id_type: UserIdType = "integer" if row.user_id_type == "integer" else "string"
    experiment_type: ExperimentType = "aa" if row.experiment_type == "aa" else "ab"
    return RunRecord(
        id=row.id,
        created_by=row.created_by,
        created_at=row.created_at,
        prepared_cohort_id=row.prepared_cohort_id,
        nl_request=row.nl_request,
        spec=dict(row.spec),
        spec_hash=row.spec_hash,
        sql=row.sql,
        sql_params=dict(row.sql_params),
        sql_hash=row.sql_hash,
        dialect=row.dialect,
        as_of=row.as_of,
        semantic_version=row.semantic_version,
        canonical_user_id=row.canonical_user_id,
        user_id_type=user_id_type,
        experiment_key=row.experiment_key,
        experiment_type=experiment_type,
        allocation=Allocation(test=row.test_percent, control=row.control_percent),
        assignment_version=row.assignment_version,
        test_count=row.test_count,
        control_count=row.control_count,
        export_columns=tuple(row.export_columns),
        download_count=row.download_count,
    )


def add_run(db: Session, run: RunRecord, members: Sequence[tuple[str, str]]) -> None:
    """Insert the run and its snapshot ``(canonical id text, group)`` in member order.

    Flushes; the caller commits together with the audit event.
    """
    db.add(
        ExperimentRun(
            id=run.id,
            created_by=run.created_by,
            created_at=run.created_at,
            prepared_cohort_id=run.prepared_cohort_id,
            nl_request=run.nl_request,
            spec=dict(run.spec),
            spec_hash=run.spec_hash,
            sql=run.sql,
            sql_params=dict(run.sql_params),
            sql_hash=run.sql_hash,
            dialect=run.dialect,
            as_of=run.as_of,
            semantic_version=run.semantic_version,
            canonical_user_id=run.canonical_user_id,
            user_id_type=run.user_id_type,
            experiment_key=run.experiment_key,
            experiment_type=run.experiment_type,
            test_percent=run.allocation.test,
            control_percent=run.allocation.control,
            assignment_version=run.assignment_version,
            test_count=run.test_count,
            control_count=run.control_count,
            export_columns=list(run.export_columns),
            download_count=run.download_count,
        )
    )
    db.flush()
    for start in range(0, len(members), INSERT_BATCH):
        db.execute(
            insert(ExperimentRunMember),
            [
                {
                    "run_id": run.id,
                    "position": position,
                    "canonical_user_id": user,
                    "assigned_group": group,
                }
                for position, (user, group) in enumerate(
                    members[start : start + INSERT_BATCH], start=start
                )
            ],
        )


def get_run(db: Session, run_id: str, *, user_id: int, read_all: bool) -> RunView | None:
    """The run if the caller may see it (creator, or ``cohort.read_all``), else None."""
    found = db.execute(
        select(ExperimentRun, User)
        .join(User, User.id == ExperimentRun.created_by)
        .where(ExperimentRun.id == run_id)
    ).one_or_none()
    if found is None:
        return None
    row, user = found
    if not read_all and row.created_by != user_id:
        return None
    return RunView(record_of(row), Creator(user.id, user.email, user.display_name))


def list_runs(
    db: Session, *, user_id: int, read_all: bool, limit: int, offset: int
) -> tuple[list[RunView], int]:
    """Visible runs, newest first, and their total."""
    query = select(ExperimentRun, User).join(User, User.id == ExperimentRun.created_by)
    count = select(func.count()).select_from(ExperimentRun)
    if not read_all:
        query = query.where(ExperimentRun.created_by == user_id)
        count = count.where(ExperimentRun.created_by == user_id)
    rows = db.execute(
        query.order_by(ExperimentRun.created_at.desc(), ExperimentRun.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    total = int(db.scalar(count) or 0)
    views = [
        RunView(record_of(row), Creator(user.id, user.email, user.display_name))
        for row, user in rows
    ]
    return views, total


def member_batches(db: Session, run_id: str, group: str, *, batch_size: int) -> Iterator[list[str]]:
    """Canonical id texts of one group, in snapshot order, ``batch_size`` at a time."""
    last = -1
    while True:
        rows = db.execute(
            select(ExperimentRunMember.position, ExperimentRunMember.canonical_user_id)
            .where(
                ExperimentRunMember.run_id == run_id,
                ExperimentRunMember.assigned_group == group,
                ExperimentRunMember.position > last,
            )
            .order_by(ExperimentRunMember.position)
            .limit(batch_size)
        ).all()
        if not rows:
            return
        last = rows[-1][0]
        yield [str(user) for _, user in rows]


def count_download(db: Session, run_id: str) -> None:
    db.execute(
        update(ExperimentRun)
        .where(ExperimentRun.id == run_id)
        .values(download_count=ExperimentRun.download_count + 1)
    )
