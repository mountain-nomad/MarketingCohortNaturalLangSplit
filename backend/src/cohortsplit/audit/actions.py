"""Audit action names (FR-A5). One constant per event type."""

# Authentication
LOGIN = "auth.login"
LOGIN_FAILED = "auth.login_failed"
LOCKOUT = "auth.lockout"
LOGOUT = "auth.logout"
PASSWORD_CHANGE = "auth.password_change"  # noqa: S105 (action name)
PASSWORD_CHANGE_FORCED = "auth.password_change_forced"  # noqa: S105 (action name)

# Users
USER_CREATE = "user.create"
USER_UPDATE = "user.update"
USER_DEACTIVATE = "user.deactivate"
USER_REACTIVATE = "user.reactivate"
USER_PASSWORD_RESET = "user.password_reset"  # noqa: S105 (action name)

# Roles, permissions and export-column grants
ROLE_CREATE = "role.create"
ROLE_UPDATE = "role.update"
ROLE_DELETE = "role.delete"
ROLE_ASSIGN = "role.assign"
ROLE_UNASSIGN = "role.unassign"

# Exports (recorded by the export authorization gate)
COHORT_EXPORT = "cohort.export"
COHORT_REDOWNLOAD = "cohort.redownload"

# Authorization denials on permission-gated endpoints
ACCESS_DENIED = "access.denied"

# Warehouse crawler runs (feature/warehouse-crawler, via AuditCrawlHook)
CRAWLER_RUN = "crawler.run"
# A crawl requested through the API: written (fail closed) before the warehouse is
# touched, or as "denied" when another crawl is running. The run itself is CRAWLER_RUN.
CRAWLER_START = "crawler.start"

# Semantic context (feature/semantic-context): business-context edits, review decisions
SEMANTIC_CONTEXT_CREATE = "semantic_context.create"
SEMANTIC_CONTEXT_UPDATE = "semantic_context.update"
SEMANTIC_CONTEXT_DELETE = "semantic_context.delete"
USE_CASE_CONFIRM = "use_case.confirm"
USE_CASE_REJECT = "use_case.reject"
USE_CASE_EDIT = "use_case.edit"
