"""SemanticContextProvider: the one source of LLM context for the cohort compiler (BR-7).

Usage (feature/cohort-compiler)::

    provider = SemanticContextProvider(
        session_factory,
        crawler_settings=load_crawler_settings(),
        export_grants=RoleExportGrants(session_factory),
    )
    snapshot = provider.snapshot()        # read in one REPEATABLE READ transaction
    snapshot.semantic_version             # record with the cohort run and export
    snapshot.canonical_user_id            # "schema.table.column" or None -> refuse the run
    snapshot.business_context             # human definitions whose references resolve
    snapshot.confirmed_use_cases          # few-shot examples (confirmed only)
    snapshot.tables                       # raw schema metadata + policy-permitted samples

Call :meth:`snapshot` once per interpretation and use only its fields, so the version
recorded with a run is the version of exactly the context the LLM saw.
"""

from sqlalchemy.orm import Session, sessionmaker

from cohortsplit.crawler.sampling import ExportGrantProvider, SamplingPolicy
from cohortsplit.crawler.settings import CrawlerSettings
from cohortsplit.semantic import repository as repo
from cohortsplit.semantic.snapshot import (
    PromptUseCase,
    SemanticContextSnapshot,
    assemble_snapshot,
)
from cohortsplit.semantic.version import SemanticVersion


def current_semantic_version(db: Session) -> SemanticVersion:
    """The semantic version of the content stored now (read in ``db``'s transaction)."""
    return repo.compute_current_version(db)


def read_snapshot(db: Session, policy: SamplingPolicy) -> SemanticContextSnapshot:
    """Assemble the snapshot from ``db`` (the caller controls the transaction)."""
    return assemble_snapshot(
        entries=[repo.entry_content(e) for e in repo.list_entries(db)],
        use_cases=repo.use_case_rows(db),
        generated_docs=repo.generated_docs(db),
        policy=policy,
    )


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

    def sampling_policy(self) -> SamplingPolicy:
        """The current sampling policy: switch, denylist and today's export grants."""
        return self._crawler_settings.sampling_policy(self._export_grants.export_granted_columns())

    def snapshot(self) -> SemanticContextSnapshot:
        policy = self.sampling_policy()
        with self._session_factory() as db:
            if db.get_bind().dialect.name == "postgresql":
                # One consistent view: the version always matches the content returned.
                db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            return read_snapshot(db, policy)

    def confirmed_use_cases_for_prompt(self) -> tuple[PromptUseCase, ...]:
        return self.snapshot().confirmed_use_cases

    def current_semantic_version(self) -> str:
        with self._session_factory() as db:
            return current_semantic_version(db).version

    def canonical_user_id(self) -> str | None:
        return self.snapshot().canonical_user_id
