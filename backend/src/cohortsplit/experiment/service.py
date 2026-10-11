"""Split, history and export orchestration (FR-8, FR-9, FR-9a; campaign steps 6-8).

Split (``cohort.create``):

1. the caller's unexpired prepared cohort (404 otherwise);
2. one semantic snapshot; the semantic version must equal the prepared cohort's (409,
   re-interpret — ruling E3) and a canonical user id must be configured;
3. requested export columns: granted (``effective_export_columns``) and in the canonical
   user table;
4. membership re-executed with ``execute_membership`` (AST re-validation, read-only
   executor, row cap, timeout); an empty result is refused;
5. deterministic assignment; the run and its snapshot (canonical id + group only) are
   stored with a sensitive ``cohort.split`` audit event in the same transaction.

Download (``cohort.export``): visible runs only (creator or ``cohort.read_all``; others
are 404). Membership comes from the snapshot — runs are never re-executed. Extra columns
are re-checked by ``PolicyService.authorize_export`` right before any data is produced
(denials audited, success audited first, fail closed) and fetched from the warehouse with
SQL built here. The ZIP is built completely before it is returned: a warehouse failure
gives an error, never a partial export.

Operational logs carry ids, hashes, keys and counts — never member ids or values.
"""

import logging
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Any

from sqlalchemy.orm import Session

from cohortsplit.audit import actions
from cohortsplit.audit.service import Actor, AuditEventIn, AuditService, AuditWriteError, Outcome
from cohortsplit.auth.errors import ApiError, ServiceUnavailableError
from cohortsplit.auth.policy import ExportColumnDeniedError, PolicyService, Principal
from cohortsplit.cohort import errors as cohort_errors
from cohortsplit.cohort.environment import CohortEnvironment, QueryExecutor
from cohortsplit.cohort.service import RequestContext
from cohortsplit.cohort.store import (
    PreparedCohort,
    PreparedCohortStore,
    execute_membership,
    new_id,
)
from cohortsplit.compiler.resolve import CanonicalUserIdMissingError, JsonScalar
from cohortsplit.compiler.validator import SqlValidationError, validate_select
from cohortsplit.config import ConfigError
from cohortsplit.experiment import errors
from cohortsplit.experiment.assignment import (
    ASSIGNMENT_VERSION,
    Allocation,
    assign,
    canonical_id_text,
)
from cohortsplit.experiment.export import (
    GROUPS,
    ZipExport,
    build_manifest,
    member_columns_query,
    zip_name,
)
from cohortsplit.experiment.records import ExperimentType, RunRecord, UserIdType
from cohortsplit.experiment.store import (
    RunView,
    add_run,
    count_download,
    get_run,
    list_runs,
    member_batches,
)
from cohortsplit.semantic.snapshot import SemanticContextSnapshot
from cohortsplit.warehouse.errors import (
    QueryTimeoutError,
    RowCapExceededError,
    WarehouseError,
    WarehouseNotConfiguredError,
    WarehouseUnavailableError,
)

logger = logging.getLogger(__name__)

READ_ALL = "cohort.read_all"
# Users per extra-column query (one bound parameter each).
EXPORT_BATCH_SIZE = 1_000
# The ZIP is spooled to disk beyond this size (bounded memory).
SPOOL_MAX_BYTES = 8 * 1024 * 1024
EMPTY_GROUP_MESSAGE = (
    "The {group} group has no users with this key and allocation. Exporting requires an "
    "explicit confirmation."
)


@dataclass(frozen=True)
class SplitRequest:
    prepared_cohort_id: str
    experiment_key: str
    allocation: Allocation
    experiment_type: ExperimentType
    export_columns: Sequence[str]


@dataclass(frozen=True)
class ExportFile:
    file: IO[bytes]
    filename: str

    def chunks(self, size: int = 64 * 1024) -> Iterator[bytes]:
        try:
            self.file.seek(0)
            while chunk := self.file.read(size):
                yield chunk
        finally:
            self.file.close()


def _event(
    ctx: RequestContext,
    action: str,
    outcome: Outcome,
    *,
    target_type: str,
    target_id: str | None,
    metadata: dict[str, object],
) -> AuditEventIn:
    return AuditEventIn(
        action=action,
        outcome=outcome,
        actor=Actor.user(ctx.principal.user_id),
        occurred_at=ctx.now,
        target_type=target_type,
        target_id=target_id,
        request_id=ctx.request_id,
        metadata=metadata,
    )


def _snapshot(env: CohortEnvironment) -> SemanticContextSnapshot:
    try:
        snapshot = env.snapshot()
    except ConfigError as exc:
        raise ServiceUnavailableError("crawler_misconfigured", str(exc)) from None
    if not snapshot.tables:
        raise cohort_errors.schema_inventory_unavailable()
    return snapshot


def _user_table_columns(snapshot: SemanticContextSnapshot, user_table: str) -> frozenset[str]:
    for table in snapshot.tables:
        if table.qualified_name == user_table:
            return frozenset(column.name for column in table.columns)
    return frozenset()


def _split_columns(canonical: str, requested: Sequence[str]) -> list[str]:
    """Requested extra columns, de-duplicated in order, without the canonical id."""
    return [c for c in dict.fromkeys(requested) if c != canonical]


def _unsupported(columns: Sequence[str], user_table: str, available: frozenset[str]) -> list[str]:
    return [
        c
        for c in columns
        if c.rsplit(".", 1)[0] != user_table or c.rsplit(".", 1)[1] not in available
    ]


def _warehouse_error(exc: WarehouseError) -> tuple[str, ApiError]:
    """Map a warehouse failure to (audit code, API error)."""
    if isinstance(exc, RowCapExceededError):
        return "row_cap_exceeded", cohort_errors.RowCapError(exc.row_cap)
    if isinstance(exc, QueryTimeoutError):
        return "query_timeout", cohort_errors.QueryTimeoutApiError(str(exc))
    if isinstance(exc, WarehouseNotConfiguredError):
        return "warehouse_not_configured", ServiceUnavailableError(
            "warehouse_not_configured", str(exc)
        )
    if isinstance(exc, WarehouseUnavailableError):
        return "warehouse_unavailable", cohort_errors.WarehouseApiError(
            503, "warehouse_unavailable", str(exc)
        )
    return "warehouse_error", cohort_errors.WarehouseApiError(502, "warehouse_error", str(exc))


def summary(view: RunView) -> dict[str, Any]:
    run = view.run
    return {
        "id": run.id,
        "experiment_key": run.experiment_key,
        "experiment_type": run.experiment_type,
        "allocation": {"test": run.allocation.test, "control": run.allocation.control},
        "assignment_version": run.assignment_version,
        "counts": {"test": run.test_count, "control": run.control_count, "total": run.total},
        "empty_groups": list(run.empty_groups),
        "requires_empty_group_confirmation": bool(run.empty_groups),
        "export_columns": list(run.export_columns),
        "download_count": run.download_count,
        "nl_request": run.nl_request,
        "spec_hash": run.spec_hash,
        "sql_hash": run.sql_hash,
        "semantic_version": run.semantic_version,
        "created_at": run.created_at.isoformat(),
        "created_by": {
            "id": view.creator.id,
            "email": view.creator.email,
            "display_name": view.creator.display_name,
        },
    }


def detail(view: RunView) -> dict[str, Any]:
    run = view.run
    return {
        **summary(view),
        "spec": dict(run.spec),
        "sql": run.sql,
        "sql_params": dict(run.sql_params),
        "dialect": run.dialect,
        "as_of": run.as_of.isoformat(),
        "canonical_user_id": run.canonical_user_id,
        "prepared_cohort_id": run.prepared_cohort_id,
    }


class ExperimentService:
    def __init__(self, audit: AuditService, policy: PolicyService) -> None:
        self._audit = audit
        self._policy = policy
        self._prepared = PreparedCohortStore()

    # -- split ---------------------------------------------------------------------------

    def split(
        self, db: Session, env: CohortEnvironment, request: SplitRequest, ctx: RequestContext
    ) -> dict[str, Any]:
        prepared = self._prepared.get(
            db, request.prepared_cohort_id, user_id=ctx.principal.user_id, now=ctx.now
        )
        if prepared is None:
            raise errors.PreparedCohortNotFoundError()
        base = self._split_metadata(prepared, request)

        def refuse(code: str, error: Exception, outcome: Outcome = "error") -> Exception:
            self._refused(
                ctx, actions.COHORT_SPLIT, "prepared_cohort", prepared.id, base, code, outcome
            )
            return error

        snapshot = _snapshot(env)
        if snapshot.semantic_version != prepared.semantic_version:
            raise refuse(
                "semantic_version_changed",
                cohort_errors.semantic_version_changed(
                    prepared.semantic_version, snapshot.semantic_version
                ),
            )
        canonical = snapshot.canonical_user_id
        if canonical is None:
            raise refuse(
                "canonical_user_id_missing",
                cohort_errors.canonical_user_id_missing(str(CanonicalUserIdMissingError())),
            )
        user_table = canonical.rsplit(".", 1)[0]
        columns = _split_columns(canonical, request.export_columns)
        allowed = self._policy.effective_export_columns(db, ctx.principal)
        denied = [c for c in columns if not allowed.allows(c)]
        if denied:
            raise refuse("export_column_denied", ExportColumnDeniedError(denied), "denied")
        unsupported = _unsupported(columns, user_table, _user_table_columns(snapshot, user_table))
        if unsupported:
            raise refuse(
                "export_column_unsupported",
                cohort_errors.ExportColumnUnsupportedError(unsupported, user_table),
                "denied",
            )

        db.rollback()  # release the appdb connection during warehouse work
        try:
            executor = env.executor()
            member_ids = execute_membership(
                executor,
                prepared,
                allowed_tables=frozenset(t.qualified_name for t in snapshot.tables),
            )
        except SqlValidationError as exc:
            logger.error("experiment.split sql_validation_failed defect reason=%s", exc.reason)
            raise refuse("sql_validation_failed", cohort_errors.SqlValidationApiError()) from None
        except WarehouseError as exc:
            code, error = _warehouse_error(exc)
            raise refuse(code, error) from None

        members = list(dict.fromkeys(member_ids))
        if not members:
            raise refuse("cohort_empty", errors.cohort_empty())
        try:
            texts = [canonical_id_text(m) for m in members]
        except (TypeError, ValueError):
            kinds = sorted({type(m).__name__ for m in members})
            raise refuse(
                "unsupported_user_id", errors.UnsupportedUserIdError(", ".join(kinds))
            ) from None
        user_id_type: UserIdType = (
            "integer"
            if all(isinstance(m, int) and not isinstance(m, bool) for m in members)
            else "string"
        )
        snapshot_rows = [
            (text, assign(request.experiment_key, text, request.allocation)) for text in texts
        ]
        test_count = sum(1 for _, group in snapshot_rows if group == "test")
        run = RunRecord(
            id=new_id(),
            created_by=ctx.principal.user_id,
            created_at=ctx.now,
            prepared_cohort_id=prepared.id,
            nl_request=prepared.nl_request,
            spec=dict(prepared.spec),
            spec_hash=prepared.spec_hash,
            sql=prepared.sql,
            sql_params=dict(prepared.params),
            sql_hash=prepared.sql_hash,
            dialect=prepared.dialect,
            as_of=prepared.as_of,
            semantic_version=prepared.semantic_version,
            canonical_user_id=canonical,
            user_id_type=user_id_type,
            experiment_key=request.experiment_key,
            experiment_type=request.experiment_type,
            allocation=request.allocation,
            assignment_version=ASSIGNMENT_VERSION,
            test_count=test_count,
            control_count=len(snapshot_rows) - test_count,
            export_columns=tuple(columns),
            download_count=0,
        )
        add_run(db, run, snapshot_rows)
        counts = {"test": run.test_count, "control": run.control_count, "total": run.total}
        # Sensitive: in the run's transaction, so a failed audit write refuses the split.
        self._audit.record(
            db,
            _event(
                ctx,
                actions.COHORT_SPLIT,
                "success",
                target_type="experiment_run",
                target_id=run.id,
                metadata={**base, "export_columns": columns, "counts": counts},
            ),
            sensitive=True,
        )
        db.commit()
        logger.info(
            "experiment.split request_id=%s run_id=%s experiment_key=%s type=%s "
            "allocation=%d/%d test=%d control=%d spec_hash=%s semantic_version=%s",
            ctx.request_id,
            run.id,
            run.experiment_key,
            run.experiment_type,
            run.allocation.test,
            run.allocation.control,
            run.test_count,
            run.control_count,
            run.spec_hash,
            run.semantic_version,
        )
        view = get_run(db, run.id, user_id=ctx.principal.user_id, read_all=True)
        assert view is not None  # noqa: S101 (just committed)
        warnings = [
            {
                "code": "empty_group",
                "group": group,
                "message": EMPTY_GROUP_MESSAGE.format(group=group),
            }
            for group in run.empty_groups
        ]
        return {**summary(view), "preview_count": prepared.row_count, "warnings": warnings}

    @staticmethod
    def _split_metadata(prepared: PreparedCohort, request: SplitRequest) -> dict[str, object]:
        return {
            "prepared_cohort_id": prepared.id,
            "nl_request": prepared.nl_request,
            "spec_hash": prepared.spec_hash,
            "sql_hash": prepared.sql_hash,
            "semantic_version": prepared.semantic_version,
            "experiment_key": request.experiment_key,
            "experiment_type": request.experiment_type,
            "allocation": {"test": request.allocation.test, "control": request.allocation.control},
            "assignment_version": ASSIGNMENT_VERSION,
            "requested_export_columns": list(request.export_columns),
        }

    def _refused(
        self,
        ctx: RequestContext,
        action: str,
        target_type: str,
        target_id: str | None,
        base: dict[str, object],
        code: str,
        outcome: Outcome,
    ) -> None:
        try:
            self._audit.record_detached(
                _event(
                    ctx,
                    action,
                    outcome,
                    target_type=target_type,
                    target_id=target_id,
                    metadata={**base, "error": code},
                ),
                sensitive=outcome == "denied",
            )
        except AuditWriteError:
            logger.error("%s %s could not be audited; refusing anyway", action, code)
        logger.info(
            "%s request_id=%s outcome=%s error=%s target=%s",
            action,
            ctx.request_id,
            outcome,
            code,
            target_id,
        )

    # -- history -------------------------------------------------------------------------

    @staticmethod
    def _read_all(principal: Principal) -> bool:
        return principal.has(READ_ALL)

    def list_runs(
        self, db: Session, ctx: RequestContext, *, limit: int, offset: int
    ) -> dict[str, Any]:
        read_all = self._read_all(ctx.principal)
        views, total = list_runs(
            db, user_id=ctx.principal.user_id, read_all=read_all, limit=limit, offset=offset
        )
        return {
            "items": [summary(v) for v in views],
            "total": total,
            "limit": limit,
            "offset": offset,
            "scope": "all" if read_all else "own",
        }

    def _visible(self, db: Session, ctx: RequestContext, run_id: str) -> RunView:
        if not _is_run_id(run_id):
            raise errors.RunNotFoundError()
        view = get_run(
            db, run_id, user_id=ctx.principal.user_id, read_all=self._read_all(ctx.principal)
        )
        if view is None:
            raise errors.RunNotFoundError()
        return view

    def get_run(self, db: Session, ctx: RequestContext, run_id: str) -> dict[str, Any]:
        return detail(self._visible(db, ctx, run_id))

    # -- download ------------------------------------------------------------------------

    def download(
        self,
        db: Session,
        env: CohortEnvironment,
        run_id: str,
        *,
        columns: Sequence[str] | None,
        confirm_empty_group: bool,
        ctx: RequestContext,
    ) -> ExportFile:
        run = self._visible(db, ctx, run_id).run
        extra = _split_columns(
            run.canonical_user_id, run.export_columns if columns is None else columns
        )
        if run.empty_groups and not confirm_empty_group:
            raise errors.empty_group_confirmation_required(run.empty_groups)
        user_table, user_key = run.canonical_user_id.rsplit(".", 1)
        allowed_tables: frozenset[str] = frozenset()
        if extra:
            snapshot = _snapshot(env)
            if snapshot.canonical_user_id != run.canonical_user_id:
                raise errors.canonical_user_id_changed(
                    run.canonical_user_id, snapshot.canonical_user_id
                )
            unsupported = _unsupported(extra, user_table, _user_table_columns(snapshot, user_table))
            if unsupported:
                raise cohort_errors.ExportColumnUnsupportedError(unsupported, user_table)
            allowed_tables = frozenset(t.qualified_name for t in snapshot.tables)

        redownload = run.download_count > 0
        action = actions.COHORT_REDOWNLOAD if redownload else actions.COHORT_EXPORT
        # The export gate, immediately before any export data is produced (FR-A4, FR-A5):
        # denials are audited and raised; a failed success event refuses the export.
        self._policy.authorize_export(
            db,
            ctx.principal,
            columns=extra,
            run_id=run.id,
            redownload=redownload,
            row_counts={"test": run.test_count, "control": run.control_count},
            request_id=ctx.request_id,
            now=ctx.now,
        )
        spool: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES)  # noqa: SIM115
        try:
            executor = env.executor() if extra else None
            missing = self._write_zip(
                db,
                spool,
                run,
                extra,
                executor=executor,
                allowed_tables=allowed_tables,
                user_key=user_key,
                exported_by=ctx.principal.user_id,
                redownload=redownload,
                now=ctx.now,
            )
        except SqlValidationError as exc:
            spool.close()
            logger.error("experiment.export sql_validation_failed defect reason=%s", exc.reason)
            self._export_failed(ctx, action, run, extra, "sql_validation_failed")
            raise cohort_errors.SqlValidationApiError() from None
        except WarehouseError as exc:
            spool.close()
            code, error = _warehouse_error(exc)
            self._export_failed(ctx, action, run, extra, code)
            raise error from None
        except BaseException:
            spool.close()
            raise
        count_download(db, run.id)
        db.commit()
        logger.info(
            "experiment.export request_id=%s run_id=%s redownload=%s columns=%s test=%d "
            "control=%d missing=%s",
            ctx.request_id,
            run.id,
            redownload,
            ",".join(extra) or "-",
            run.test_count,
            run.control_count,
            "-" if missing is None else f"{missing['test']}/{missing['control']}",
        )
        return ExportFile(spool, zip_name(run.experiment_key))

    def _write_zip(
        self,
        db: Session,
        spool: IO[bytes],
        run: RunRecord,
        extra: Sequence[str],
        *,
        executor: QueryExecutor | None,
        allowed_tables: frozenset[str],
        user_key: str,
        exported_by: int,
        redownload: bool,
        now: datetime,
    ) -> dict[str, int] | None:
        user_table = run.canonical_user_id.rsplit(".", 1)[0]
        names = [c.rsplit(".", 1)[1] for c in extra]
        header = [user_key, *names]
        missing: dict[str, int] = dict.fromkeys(GROUPS, 0)
        validated: set[str] = set()

        def typed(user: str) -> JsonScalar:
            return int(user) if run.user_id_type == "integer" else user

        def rows(group: str) -> Iterator[Sequence[object]]:
            for batch in member_batches(db, run.id, group, batch_size=EXPORT_BATCH_SIZE):
                if executor is None:
                    yield from ([typed(user)] for user in batch)
                    continue
                query = member_columns_query(user_table, user_key, names, [typed(u) for u in batch])
                if query.sql not in validated:
                    validate_select(query.sql, allowed_tables=allowed_tables)
                    validated.add(query.sql)
                result = executor.execute(query.sql, query.params, row_cap=len(batch))
                found = {_id_text(row[0]): row[1:] for row in result.rows}
                for user in batch:
                    values = found.get(user)
                    if values is None:
                        missing[group] += 1
                        values = (None,) * len(names)
                    yield [typed(user), *values]

        archive = ZipExport(spool, run.experiment_key)
        try:
            for group in GROUPS:
                archive.write_group(group, header, rows(group))
            reported = dict(missing) if executor is not None else None
            archive.write_manifest(
                build_manifest(
                    run,
                    exported_at=now,
                    exported_by=exported_by,
                    redownload=redownload,
                    columns=list(extra),
                    missing=reported,
                )
            )
        finally:
            archive.close()
        return reported

    def _export_failed(
        self,
        ctx: RequestContext,
        action: str,
        run: RunRecord,
        columns: Sequence[str],
        code: str,
    ) -> None:
        self._refused(
            ctx,
            action,
            "cohort_run",
            run.id,
            {"columns": list(columns)},
            code,
            "error",
        )


def _id_text(value: object) -> str:
    try:
        return canonical_id_text(value)
    except (TypeError, ValueError):
        return str(value)


def _is_run_id(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)
