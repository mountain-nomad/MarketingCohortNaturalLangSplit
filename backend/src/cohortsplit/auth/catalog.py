"""Permission catalog (FR-A3).

The catalog is code + migration data: adding a permission requires a release, adding a
custom role never does. Business roles are data and never appear here.

``user.*`` and ``role.*`` are held only by the protected Admin system role and cannot
be granted to custom roles (resolved decision: closes the role.update + role.assign
privilege-escalation path).
"""

from dataclasses import dataclass

ADMIN_ROLE_NAME = "Admin"


@dataclass(frozen=True)
class PermissionDef:
    key: str
    area: str
    description: str

    @property
    def grantable(self) -> bool:
        """Whether a custom role may hold this permission."""
        return not self.key.startswith(ADMIN_ONLY_PREFIXES)


ADMIN_ONLY_PREFIXES = ("user.", "role.")

PERMISSION_CATALOG: tuple[PermissionDef, ...] = (
    PermissionDef("user.read", "Users", "List and view users"),
    PermissionDef("user.create", "Users", "Create users"),
    PermissionDef("user.update", "Users", "Edit users"),
    PermissionDef("user.deactivate", "Users", "Deactivate and reactivate users"),
    PermissionDef("user.reset_password", "Users", "Reset user passwords"),
    PermissionDef("role.read", "Roles", "List and view roles"),
    PermissionDef("role.create", "Roles", "Create roles"),
    PermissionDef("role.update", "Roles", "Edit roles, permissions and export grants"),
    PermissionDef("role.delete", "Roles", "Delete roles"),
    PermissionDef("role.assign", "Roles", "Assign roles to users"),
    PermissionDef("semantic_context.read", "Semantic layer", "View business context and docs"),
    PermissionDef("semantic_context.edit", "Semantic layer", "Edit business context"),
    PermissionDef("use_case.review", "Semantic layer", "Review generated use cases"),
    PermissionDef("crawler.run", "Semantic layer", "Run the warehouse crawler"),
    PermissionDef("cohort.create", "Cohorts", "Interpret, preview and split cohorts"),
    PermissionDef("cohort.export", "Cohorts", "Download and re-download cohort exports"),
    PermissionDef("cohort.read_all", "Cohorts", "See everyone's cohort runs"),
    PermissionDef("audit.read", "Audit", "Read the audit log"),
)

ALL_PERMISSIONS: frozenset[str] = frozenset(p.key for p in PERMISSION_CATALOG)
GRANTABLE_PERMISSIONS: frozenset[str] = frozenset(p.key for p in PERMISSION_CATALOG if p.grantable)
ADMIN_ONLY_PERMISSIONS: frozenset[str] = ALL_PERMISSIONS - GRANTABLE_PERMISSIONS
