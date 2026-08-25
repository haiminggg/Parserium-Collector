import json

from parserium_collector.cli.export_openapi import render_contract


def test_rendered_openapi_is_deterministic_and_contains_public_routes() -> None:
    first = render_contract()
    second = render_contract()

    assert first == second
    document = json.loads(first)
    assert sorted(document["paths"]) == [
        "/api/v1/discovery/search",
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/health/status",
        "/api/v1/session",
        "/api/v1/session/logout",
        "/api/v1/session/pair",
        "/api/v1/session/status",
    ]
