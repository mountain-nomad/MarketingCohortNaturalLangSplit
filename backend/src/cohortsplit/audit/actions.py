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
