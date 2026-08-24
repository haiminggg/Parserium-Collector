import json

from parserium_collector.cli.export_openapi import render_contract


def test_rendered_openapi_is_deterministic_and_contains_health_routes() -> None:
    first = render_contract()
    second = render_contract()

    assert first == second
    document = json.loads(first)
    assert sorted(document["paths"]) == [
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/health/status",
    ]
