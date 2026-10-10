"""Every cohort endpoint and the permission it requires (AC-29).

``tests/auth/test_endpoint_security.py`` includes these in its route inventory, so an
endpoint added without being listed here fails the inventory test.
"""

COHORT_GATED: dict[tuple[str, str], str] = {
    ("POST", "/api/cohorts/interpret"): "cohort.create",
    ("POST", "/api/cohorts/preview"): "cohort.create",
}
