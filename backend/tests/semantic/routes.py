"""Every semantic-context endpoint and the permission it requires (AC-29).

``tests/auth/test_endpoint_security.py`` includes these in its route inventory, so an
endpoint added without being listed here fails the inventory test.
"""

SEMANTIC_GATED: dict[tuple[str, str], str] = {
    ("GET", "/api/semantic/docs"): "semantic_context.read",
    ("GET", "/api/semantic/business-context"): "semantic_context.read",
    ("POST", "/api/semantic/business-context"): "semantic_context.edit",
    ("PUT", "/api/semantic/business-context/{key}"): "semantic_context.edit",
    ("DELETE", "/api/semantic/business-context/{key}"): "semantic_context.edit",
    ("GET", "/api/semantic/use-cases"): "semantic_context.read",
    ("POST", "/api/semantic/use-cases/{use_case_id}/confirm"): "use_case.review",
    ("POST", "/api/semantic/use-cases/{use_case_id}/reject"): "use_case.review",
    ("PUT", "/api/semantic/use-cases/{use_case_id}"): "use_case.review",
    ("GET", "/api/semantic/version"): "semantic_context.read",
    ("GET", "/api/semantic/versions"): "semantic_context.read",
    ("POST", "/api/crawler/runs"): "crawler.run",
    ("GET", "/api/crawler/runs"): "semantic_context.read",
}
