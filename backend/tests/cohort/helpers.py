"""Cohort API test helpers: fake warehouse executor, environment, specs and clients."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Session

from cohortsplit.cohort.environment import CohortEnvironment, LlmConnection, get_cohort_environment
from cohortsplit.compiler.validator import validate_select
from cohortsplit.semantic.snapshot import SemanticContextSnapshot
from cohortsplit.warehouse.models import QueryResult
from tests.auth.helpers import ApiClient, FakeClock, make_role, make_user
from tests.llm_fakes import FakeChatClient, interpretation
from tests.unit.compiler.fixtures import ADDRESSES_HOP, CARTS_HOP, shop_snapshot

INTERPRET = "/api/cohorts/interpret"
PREVIEW = "/api/cohorts/preview"

AC2_REQUEST = "Users from Kazakhstan who abandoned a cart and never paid for an order"
AC2_SPEC: dict[str, Any] = {
    "spec_version": "1",
    "entity": "user",
    "where": {
        "type": "all_of",
        "conditions": [
            {
                "type": "related",
                "table": "public.addresses",
                "path": [ADDRESSES_HOP],
                "filters": [
                    {"column": "public.addresses.country_code", "operator": "=", "value": "KZ"}
                ],
            },
            {
                "type": "related",
                "table": "public.carts",
                "path": [CARTS_HOP],
                "filters": [
                    {"column": "public.carts.status", "operator": "=", "value": "abandoned"}
                ],
            },
            {"type": "not", "condition": {"type": "metric", "term": "paid_order"}},
        ],
    },
}
AC2_READABLE = (
    "has addresses with country_code = KZ AND has carts with status = abandoned "
    "AND NOT has paid_order"
)


@dataclass
class Statement:
    sql: str
    params: Mapping[str, object] | None
    row_cap: int | None


@dataclass
class FakeExecutor:
    """Answers the compiler's query shapes from in-memory members; records every statement.

    Every statement is checked with the AST validator first, like the real path.
    """

    members: list[int] = field(default_factory=lambda: [3, 1, 2])
    columns: dict[str, dict[int, object]] = field(default_factory=dict)
    existing: dict[str, set[object]] = field(default_factory=dict)
    error: Exception | None = None
    row_cap: int = 1_000_000
    statement_timeout_seconds: float = 30.0
    statements: list[Statement] = field(default_factory=list)
    allowed_tables: frozenset[str] = frozenset()

    def execute(
        self, sql: str, params: Mapping[str, object] | None = None, *, row_cap: int | None = None
    ) -> QueryResult:
        self.statements.append(Statement(sql, params, row_cap))
        if self.allowed_tables:
            validate_select(sql, allowed_tables=self.allowed_tables)
        if sql.startswith("SELECT DISTINCT t."):
            values = [v for v in (params or {}).values() if v in self.existing_values(sql)]
            return QueryResult(columns=("v",), rows=tuple((v,) for v in values))
        if self.error is not None:
            raise self.error
        members = sorted(self.members)
        if sql.startswith('SELECT COUNT(*) AS "total"'):
            names = self._selected(sql)
            nulls = [sum(1 for m in members if self.columns[n].get(m) is None) for n in names]
            return QueryResult(columns=(), rows=((len(members), *nulls),))
        if " LIMIT " in sql:
            limit = int(sql.rsplit(" LIMIT ", 1)[1])
            names = self._selected(sql)
            rows = tuple((m, *(self.columns[n].get(m) for n in names)) for m in members[:limit])
            return QueryResult(columns=("canonical_user_id", *names), rows=rows)
        cap = self.row_cap if row_cap is None else row_cap
        if len(members) > cap:
            from cohortsplit.warehouse.errors import RowCapExceededError

            raise RowCapExceededError(cap)
        return QueryResult(columns=("canonical_user_id",), rows=tuple((m,) for m in members))

    def existing_values(self, sql: str) -> set[object]:
        for column, values in self.existing.items():
            name = column.rsplit(".", 1)[1]
            if f't."{name}"' in sql:
                return values
        return set()

    def _selected(self, sql: str) -> list[str]:
        return [c for c in self.columns if f'"{c.rsplit(".", 1)[1]}"' in sql]

    def cohort_statements(self) -> list[Statement]:
        return [s for s in self.statements if not s.sql.startswith("SELECT DISTINCT t.")]


@dataclass
class Environment:
    llm: FakeChatClient
    executor: FakeExecutor
    snapshots: list[SemanticContextSnapshot]
    max_attempts: int = 3
    llm_error: Exception | None = None
    executor_error: Exception | None = None

    def snapshot(self) -> SemanticContextSnapshot:
        return self.snapshots[0]

    def as_environment(self) -> CohortEnvironment:
        def llm() -> LlmConnection:
            if self.llm_error is not None:
                raise self.llm_error
            return LlmConnection(self.llm, self.max_attempts)

        def executor() -> FakeExecutor:
            if self.executor_error is not None:
                raise self.executor_error
            return self.executor

        return CohortEnvironment(snapshot=self.snapshot, llm=llm, executor=executor)


def install_environment(
    app: FastAPI,
    *,
    replies: Sequence[Any] = (interpretation(AC2_SPEC),),
    snapshot: SemanticContextSnapshot | None = None,
    executor: FakeExecutor | None = None,
) -> Environment:
    snap = snapshot or shop_snapshot()
    fake_executor = executor or FakeExecutor()
    fake_executor.allowed_tables = frozenset(t.qualified_name for t in snap.tables)
    env = Environment(FakeChatClient(list(replies)), fake_executor, [snap])
    app.dependency_overrides[get_cohort_environment] = env.as_environment
    return env


def marketer(
    app: FastAPI,
    db: Session,
    clock: FakeClock,
    *,
    email: str = "marketer@example.com",
    permissions: Iterable[str] = ("cohort.create",),
    export_columns: Iterable[str] = (),
) -> ApiClient:
    role = make_role(
        db, f"Role {email}", sorted(set(permissions)), list(export_columns), now=clock.now
    )
    make_user(db, email, now=clock.now, roles=[role])
    client = ApiClient(app)
    assert client.login(email).status_code == 200
    return client


def interpret_ok(client: ApiClient, request: str = AC2_REQUEST) -> dict[str, Any]:
    response = client.post(INTERPRET, json={"nl_request": request})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["kind"] == "interpretation", body
    return body


def confirm(interpreted: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "interpretation_id": interpreted["interpretation_id"],
        "spec_hash": interpreted["spec_hash"],
        "semantic_version": interpreted["semantic_version"],
        **extra,
    }


Reply = str | Exception | Callable[..., str]
