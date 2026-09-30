import json

from parserium_collector.cli.export_openapi import render_contract


def test_rendered_openapi_is_deterministic_and_contains_public_routes() -> None:
    first = render_contract()
    second = render_contract()

    assert first == second
    document = json.loads(first)
    assert sorted(document["paths"]) == [
        "/api/v1/auth/callback",
        "/api/v1/auth/login",
        "/api/v1/collection/jobs",
        "/api/v1/collection/jobs/completed",
        "/api/v1/collection/jobs/{job_id}/retry",
        "/api/v1/discovery/analyses/{candidate_id}/preview",
        "/api/v1/discovery/search",
        "/api/v1/discovery/searches",
        "/api/v1/discovery/searches/{session_id}",
        "/api/v1/documents",
        "/api/v1/documents/{document_id}",
        "/api/v1/documents/{document_id}/download",
        "/api/v1/documents/{document_id}/exports",
        "/api/v1/exports",
        "/api/v1/firecrawl/connections",
        "/api/v1/firecrawl/connections/{connection_id}",
        "/api/v1/firecrawl/connections/{connection_id}/credential",
        "/api/v1/firecrawl/connections/{connection_id}/test",
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/health/status",
        "/api/v1/session",
        "/api/v1/session/logout",
        "/api/v1/session/pair",
        "/api/v1/session/status",
        "/api/v1/session/workspace",
    ]
    jobs = document["paths"]["/api/v1/collection/jobs"]["get"]
    stored_documents = document["paths"]["/api/v1/documents"]["get"]
    assert {
        parameter["name"] for parameter in jobs["parameters"] if parameter["in"] == "query"
    } == {
        "cursor",
        "limit",
    }
    assert {
        parameter["name"]
        for parameter in stored_documents["parameters"]
        if parameter["in"] == "query"
    } == {
        "cursor",
        "limit",
    }
    assert jobs["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CollectionJobPageResponse"
    }
    assert stored_documents["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/StoredDocumentPageResponse"
    }

    schemas = document["components"]["schemas"]
    session_response = schemas["SessionResponse"]
    assert {
        "authentication_mode",
        "user",
        "workspace",
        "workspaces",
    } <= set(session_response["required"])
    assert schemas["PublicSessionStatus"]["properties"]["status"]["const"] == ("pairing_required")
    assert schemas["LoginRequiredSession"]["properties"]["status"]["const"] == ("login_required")
    assert "provider_label" in schemas["LoginRequiredSession"]["required"]

    session_status = document["paths"]["/api/v1/session/status"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert {option["$ref"] for option in session_status["anyOf"]} == {
        "#/components/schemas/LoginRequiredSession",
        "#/components/schemas/PublicSessionStatus",
        "#/components/schemas/SessionResponse",
    }

    connection_summary = schemas["ConnectionSummary"]
    assert not {
        "credential",
        "credential_envelope",
        "credential_revision",
        "ciphertext",
        "wrapped_data_key",
    } & set(connection_summary["properties"])
    assert schemas["CreateConnectionRequest"]["properties"]["credential"]["writeOnly"] is True
    assert schemas["ReplaceCredentialRequest"]["properties"]["credential"]["writeOnly"] is True
    assert document["paths"]["/api/v1/firecrawl/connections"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ConnectionListResponse"}
    for path, method, response_status in (
        ("/api/v1/firecrawl/connections", "post", "201"),
        ("/api/v1/firecrawl/connections/{connection_id}", "patch", "200"),
        ("/api/v1/firecrawl/connections/{connection_id}/credential", "put", "200"),
        ("/api/v1/firecrawl/connections/{connection_id}/test", "post", "200"),
    ):
        assert document["paths"][path][method]["responses"][response_status]["content"][
            "application/json"
        ]["schema"] == {"$ref": "#/components/schemas/ConnectionSummary"}
