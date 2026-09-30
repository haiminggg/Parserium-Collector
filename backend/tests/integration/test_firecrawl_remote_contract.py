import os
import ssl
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from parserium_collector.adapters.firecrawl.contracts import MetadataSearchRequest
from parserium_collector.features.firecrawl_connections.endpoints import FIRECRAWL_CLOUD_ORIGIN
from parserium_collector.features.firecrawl_connections.executor import SecureFirecrawlExecutor
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionType,
    ResolvedConnection,
)
from parserium_collector.settings import Settings

REMOTE_CONNECTION_ID = UUID("30000000-0000-4000-8000-000000000081")
CLOUD_CONNECTION_ID = UUID("30000000-0000-4000-8000-000000000082")
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000081")


def _required_path(name: str) -> Path:
    path = Path(os.environ[name])
    if not path.is_file():
        raise RuntimeError(f"{name} must identify a readable verification file.")
    return path


def _credential() -> str:
    return _required_path("TEST_FIRECRAWL_BEARER_FILE").read_text(encoding="utf-8")


def _origin() -> str:
    return os.environ.get("TEST_FIRECRAWL_ORIGIN", "https://test-firecrawl:9444")


def _response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "id": "verification-search",
            "data": {
                "web": [
                    {
                        "url": "http://test-origin:8090/investment-table.pdf",
                        "title": "Deterministic investment table",
                        "description": "Test-only valid PDF candidate",
                    },
                    {
                        "url": "http://test-origin:8092/blocked-investment-table.pdf",
                        "title": "Blocked investment table",
                        "description": "Test-only prohibited-port candidate",
                    },
                ]
            },
            "creditsUsed": 0,
        },
    )


async def test_tls_fixture_rejects_missing_and_wrong_bearer_without_echoing_it() -> None:
    origin = _origin()
    ca_bundle = _required_path("TEST_FIRECRAWL_CA_BUNDLE_FILE")
    credential = _credential()
    request = MetadataSearchRequest(query="verification report", limit=1)
    ssl_context = ssl.create_default_context(cafile=str(ca_bundle))

    async with httpx.AsyncClient(
        base_url=origin,
        verify=ssl_context,
        timeout=5,
    ) as client:
        missing = await client.post("/v2/search", json=request.firecrawl_body())
        wrong = await client.post(
            "/v2/search",
            json=request.firecrawl_body(),
            headers={"Authorization": "Bearer wrong-test-value"},
        )
        accepted = await client.post(
            "/v2/search",
            json=request.firecrawl_body(),
            headers={"Authorization": f"Bearer {credential}"},
        )
        state = await client.get("/test-state")

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert credential not in missing.text
    assert credential not in wrong.text
    assert accepted.status_code == 200
    assert accepted.json()["id"] == "verification-search"
    assert state.json() == {"authorization_matched": True}

    async with httpx.AsyncClient(timeout=2) as insecure_client:
        with pytest.raises(httpx.TransportError):
            await insecure_client.get(origin.replace("https://", "http://") + "/health")


async def test_cloud_and_tls_remote_executors_share_the_metadata_contract() -> None:
    origin = _origin()
    credential = _credential()
    ca_bundle = _required_path("TEST_FIRECRAWL_CA_BUNDLE_FILE")
    assert _required_path("SSL_CERT_FILE").resolve() == ca_bundle.resolve()
    request = MetadataSearchRequest(query="verification report", limit=1)
    cloud_capture: dict[str, object] = {}

    def cloud_handler(request_value: httpx.Request) -> httpx.Response:
        cloud_capture["origin"] = str(request_value.url.copy_with(path="", query=None)).rstrip("/")
        cloud_capture["authorization_matched"] = (
            request_value.headers.get("authorization") == f"Bearer {credential}"
        )
        return _response()

    cloud = SecureFirecrawlExecutor(
        Settings(),
        cloud_transport=httpx.MockTransport(cloud_handler),
    )
    cloud_result = await cloud.search(
        ResolvedConnection(
            id=CLOUD_CONNECTION_ID,
            workspace_id=WORKSPACE_ID,
            name="Cloud test double",
            connection_type=ConnectionType.CLOUD,
            normalized_base_url=None,
            credential=credential,
            credential_revision=1,
        ),
        request,
    )

    remote = SecureFirecrawlExecutor(
        Settings(
            firecrawl_remote_allowed_ports=(9444,),
            firecrawl_remote_private_allowlist=("test-firecrawl:9444",),
        )
    )
    remote_result = await remote.search(
        ResolvedConnection(
            id=REMOTE_CONNECTION_ID,
            workspace_id=WORKSPACE_ID,
            name="TLS remote",
            connection_type=ConnectionType.REMOTE,
            normalized_base_url=origin,
            credential=credential,
            credential_revision=1,
        ),
        request,
    )

    assert cloud_capture == {
        "origin": FIRECRAWL_CLOUD_ORIGIN,
        "authorization_matched": True,
    }
    assert cloud_result.model_dump() == remote_result.model_dump()
