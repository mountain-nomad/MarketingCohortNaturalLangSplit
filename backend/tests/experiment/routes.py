"""Every experiment endpoint and the permissions that admit it (any one of them; AC-29).

``tests/auth/test_endpoint_security.py`` includes these in its route inventory, so an
endpoint added without being listed here fails the inventory test.
"""

EXPERIMENT_GATED: dict[tuple[str, str], tuple[str, ...]] = {
    ("POST", "/api/runs"): ("cohort.create",),
    ("GET", "/api/runs"): ("cohort.create", "cohort.read_all"),
    ("GET", "/api/runs/{run_id}"): ("cohort.create", "cohort.read_all"),
    ("POST", "/api/runs/{run_id}/download"): ("cohort.export",),
}
