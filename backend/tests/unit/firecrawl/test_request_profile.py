import json

import httpx
import pytest

from parserium_collector.adapters.firecrawl.client import FirecrawlClient
from parserium_collector.adapters.firecrawl.contracts import MetadataSearchRequest
from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError


async def test_metadata_probe_omits_every_fetch_option() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "success": True,
                "id": "search-1",
                "data": {
                    "web": [
                        {
                            "url": "https://example.com/report.pdf",
                            "title": "Report",
                            "description": "Public document",
                        }
                    ]
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://firecrawl.test",
        transport=transport,
    ) as http_client:
        client = FirecrawlClient(http_client)
        result = await client.probe_metadata_search(
            MetadataSearchRequest(query="public bank report", limit=10)
        )

    assert captured == {
        "query": "public bank report",
        "limit": 10,
        "sources": [{"type": "web"}],
        "highlights": False,
        "origin": "parserium-collector",
    }
    assert result.search_id == "search-1"
    assert str(result.results[0].url) == "https://example.com/report.pdf"


async def test_metadata_probe_rejects_fetched_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "id": "search-2",
                "data": {
                    "web": [
                        {
                            "url": "https://example.com/",
                            "markdown": "Fetched page content",
                        }
                    ]
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://firecrawl.test",
        transport=transport,
    ) as http_client:
        client = FirecrawlClient(http_client)
        with pytest.raises(FirecrawlAdapterError) as error:
            await client.probe_metadata_search(MetadataSearchRequest(query="report"))

    assert error.value.code == "compatibility_mismatch"
    assert error.value.retryable is False


async def test_metadata_probe_rejects_unknown_result_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "id": "search-3",
                "data": {
                    "web": [
                        {
                            "url": "https://example.com/",
                            "content": "An unrecognized content field",
                        }
                    ]
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://firecrawl.test",
        transport=transport,
    ) as http_client:
        client = FirecrawlClient(http_client)
        with pytest.raises(FirecrawlAdapterError) as error:
            await client.probe_metadata_search(MetadataSearchRequest(query="report"))

    assert error.value.code == "schema_mismatch"
    assert error.value.retryable is False
