"""Where interpretation and preview get their inputs: the semantic snapshot, the LLM and the
read-only warehouse executor. Tests override :func:`get_cohort_environment`.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.auth.crawler_integration import RoleExportGrants
from cohortsplit.config import Settings
from cohortsplit.crawler.settings import load_crawler_settings
from cohortsplit.llm.client import ChatClient, create_chat_client
from cohortsplit.llm.settings import load_llm_settings
from cohortsplit.semantic.provider import SemanticContextProvider
from cohortsplit.semantic.snapshot import SemanticContextSnapshot
from cohortsplit.warehouse.factory import create_warehouse_adapter
from cohortsplit.warehouse.models import QueryResult


class QueryExecutor(Protocol):
    """The read-only executor contract (``cohortsplit.warehouse.ReadOnlyExecutor``)."""

    @property
    def row_cap(self) -> int: ...

    @property
    def statement_timeout_seconds(self) -> float: ...

    def execute(
        self, sql: str, params: Mapping[str, object] | None = None, *, row_cap: int | None = None
    ) -> QueryResult: ...


@dataclass(frozen=True)
class LlmConnection:
    client: ChatClient
    max_attempts: int


@dataclass(frozen=True)
class CohortEnvironment:
    snapshot: Callable[[], SemanticContextSnapshot]
    llm: Callable[[], LlmConnection]
    executor: Callable[[], QueryExecutor]


def default_environment(
    settings: Settings, session_factory: sessionmaker[Session]
) -> CohortEnvironment:
    """Production wiring: the semantic provider (one snapshot per call), the configured
    OpenAI-compatible LLM, and the read-only warehouse role only (FR-1)."""

    def snapshot() -> SemanticContextSnapshot:
        provider = SemanticContextProvider(
            session_factory,
            crawler_settings=load_crawler_settings(),
            export_grants=RoleExportGrants(session_factory),
        )
        return provider.snapshot()

    def llm() -> LlmConnection:
        llm_settings = load_llm_settings()
        return LlmConnection(create_chat_client(llm_settings), llm_settings.max_attempts)

    def executor() -> QueryExecutor:
        return create_warehouse_adapter(settings).executor

    return CohortEnvironment(snapshot=snapshot, llm=llm, executor=executor)


def get_cohort_environment(request: Request) -> CohortEnvironment:
    environment: CohortEnvironment = request.app.state.cohort.environment
    return environment
