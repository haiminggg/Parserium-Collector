import os

import httpx
import pytest

from parserium_collector.adapters.firecrawl.client import FirecrawlClient
from parserium_collector.adapters.firecrawl.contracts import MetadataSearchRequest


async def test_local_firecrawl_search_returns_metadata_without_fetched_content() -> None:
    base_url = os.environ.get("TEST_FIRECRAWL_URL")
    if base_url is None:
        pytest.skip("Set TEST_FIRECRAWL_URL to run the local Firecrawl compatibility proof.")

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(45.0),
    ) as http_client:
        result = await FirecrawlClient(http_client).probe_metadata_search(
            MetadataSearchRequest(
                query="site:example.com filetype:pdf",
                limit=1,
            )
        )

    assert result.search_id
    assert len(result.results) <= 1
