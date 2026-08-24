import json
from typing import Any

import httpx
from pydantic import ValidationError

from parserium_collector.adapters.firecrawl.contracts import (
    FirecrawlSearchEnvelope,
    MetadataSearchRequest,
    MetadataSearchResult,
)
from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError

FORBIDDEN_FETCH_KEYS = {
    "markdown",
    "html",
    "rawhtml",
    "screenshot",
    "screenshots",
}


def contains_fetched_content(value: Any) -> bool:
    if isinstance(value, dict):
        if any(str(key).lower() in FORBIDDEN_FETCH_KEYS for key in value):
            return True
        return any(contains_fetched_content(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_fetched_content(item) for item in value)
    return False


class FirecrawlClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        response_limit_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self._client = client
        self._response_limit_bytes = response_limit_bytes

    async def probe_metadata_search(
        self,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        try:
            async with self._client.stream(
                "POST",
                "/v2/search",
                json=request.firecrawl_body(),
            ) as response:
                if response.status_code >= 400:
                    retryable = response.status_code in {408, 429} or response.status_code >= 500
                    raise FirecrawlAdapterError(
                        code=f"http_{response.status_code}",
                        retryable=retryable,
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._response_limit_bytes:
                        raise FirecrawlAdapterError(
                            code="response_too_large",
                            retryable=False,
                        )
                    chunks.append(chunk)
        except FirecrawlAdapterError:
            raise
        except httpx.TimeoutException as error:
            raise FirecrawlAdapterError(code="timeout", retryable=True) from error
        except httpx.TransportError as error:
            raise FirecrawlAdapterError(code="transport_error", retryable=True) from error

        try:
            payload = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise FirecrawlAdapterError(code="invalid_json", retryable=False) from error
        if contains_fetched_content(payload):
            raise FirecrawlAdapterError(code="compatibility_mismatch", retryable=False)
        try:
            envelope = FirecrawlSearchEnvelope.model_validate(payload)
        except ValidationError as error:
            raise FirecrawlAdapterError(code="schema_mismatch", retryable=False) from error
        if not envelope.success:
            raise FirecrawlAdapterError(code="unsuccessful_response", retryable=False)
        return MetadataSearchResult(
            search_id=envelope.id,
            results=envelope.data.web,
        )
