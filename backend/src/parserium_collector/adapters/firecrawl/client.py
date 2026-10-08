import json
import ssl
from typing import Any

import httpx
from pydantic import ValidationError

from parserium_collector.adapters.firecrawl.contracts import (
    FirecrawlSearchEnvelope,
    MetadataSearchRequest,
    MetadataSearchResult,
    SearchResult,
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


def parse_metadata_search_response(
    status_code: int,
    content: bytes,
) -> MetadataSearchResult:
    if 300 <= status_code < 400:
        raise FirecrawlAdapterError(code="redirect_response", retryable=False)
    if status_code >= 400:
        retryable = status_code in {408, 429} or status_code >= 500
        raise FirecrawlAdapterError(
            code=f"http_{status_code}",
            retryable=retryable,
        )
    if not 200 <= status_code < 300:
        raise FirecrawlAdapterError(code="invalid_status", retryable=False)
    try:
        payload = json.loads(content)
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
        results=[
            SearchResult(url=item.url, title=item.title, description=item.description)
            for item in envelope.data.web
        ],
    )


def _exception_chain_contains(
    error: BaseException,
    expected: type[BaseException],
) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, expected):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
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
                if not 200 <= response.status_code < 300:
                    return parse_metadata_search_response(response.status_code, b"")
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
        except httpx.ConnectError as error:
            if _exception_chain_contains(error, ssl.SSLError):
                raise FirecrawlAdapterError(code="tls_failure", retryable=False) from error
            raise FirecrawlAdapterError(code="transport_error", retryable=True) from error
        except httpx.TransportError as error:
            raise FirecrawlAdapterError(code="transport_error", retryable=True) from error

        return parse_metadata_search_response(response.status_code, b"".join(chunks))
