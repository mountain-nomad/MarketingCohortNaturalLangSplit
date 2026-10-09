"""SemanticContextProvider for the cohort compiler. STUB: implemented in the GREEN commit."""

from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.crawler.sampling import ExportGrantProvider
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.semantic.snapshot import PromptUseCase, SemanticContextSnapshot
from cohortsplit.semantic.version import SemanticVersion


def current_semantic_version(db: Session) -> SemanticVersion:
    raise NotImplementedError


class SemanticContextProvider:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        crawler_settings: CrawlerSettings,
        export_grants: ExportGrantProvider,
    ) -> None:
        self._session_factory = session_factory
        self._crawler_settings = crawler_settings
        self._export_grants = export_grants

    def snapshot(self) -> SemanticContextSnapshot:
        raise NotImplementedError

    def confirmed_use_cases_for_prompt(self) -> tuple[PromptUseCase, ...]:
        raise NotImplementedError

    def current_semantic_version(self) -> str:
        raise NotImplementedError

    def canonical_user_id(self) -> str | None:
        raise NotImplementedError
