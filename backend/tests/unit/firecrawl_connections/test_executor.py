import ssl
from collections.abc import Callable
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pytest
from httpcore import ReadTimeout

from parserium_collector.adapters.firecrawl.contracts import (
    MetadataSearchRequest,
    MetadataSearchResult,
)
from parserium_collector.features.acquisition.errors import DestinationResolutionError
from parserium_collector.features.acquisition.network_policy import NetworkPolicy
from parserium_collector.features.firecrawl_connections.executor import (
    ConnectionExecutionError,
    RemoteSearchTransport,
    SecureFirecrawlExecutor,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionFailureCategory,
    ConnectionType,
    ResolvedConnection,
)
from parserium_collector.settings import Settings

TEST_ONLY_CREDENTIAL = "test-only-bearer-value"  # noqa: S105


class StaticResolver:
    def __init__(self, answers: tuple[str, ...] = ("93.184.216.34",)) -> None:
        self.answers = answers
        self.raise_resolution_error = False

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        if self.raise_resolution_error:
            raise DestinationResolutionError("Test double DNS failure.")
        return self.answers


class RecordingRemoteTransport(RemoteSearchTransport):
    def __init__(
        self,
        policy: NetworkPolicy,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.policy = policy
        self.failure = failure
        self.authorization_matched = False
        self.origin: str | None = None
        self.closed = False

    async def search(
        self,
        origin: str,
        credential: str,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        parts = urlsplit(origin)
        assert parts.hostname is not None
        await self.policy.approve_connection(parts.hostname, parts.port or 443)
        self.origin = origin
        self.authorization_matched = credential == TEST_ONLY_CREDENTIAL
        if self.failure is not None:
            raise self.failure
        return MetadataSearchResult(search_id="remote-search", results=[])

    async def aclose(self) -> None:
        self.closed = True


def cloud_connection() -> ResolvedConnection:
    return ResolvedConnection(
        id=UUID("10000000-0000-4000-8000-000000000005"),
        workspace_id=UUID("20000000-0000-4000-8000-000000000005"),
        name="Cloud",
        connection_type=ConnectionType.CLOUD,
        normalized_base_url=None,
        credential=TEST_ONLY_CREDENTIAL,
        credential_revision=1,
    )


def remote_connection(origin: str = "https://firecrawl.example") -> ResolvedConnection:
    return ResolvedConnection(
        id=UUID("10000000-0000-4000-8000-000000000006"),
        workspace_id=UUID("20000000-0000-4000-8000-000000000006"),
        name="Remote",
        connection_type=ConnectionType.REMOTE,
        normalized_base_url=origin,
        credential=TEST_ONLY_CREDENTIAL,
        credential_revision=1,
    )


def metadata_response(status: int = 200, content: bytes | None = None) -> httpx.Response:
    if content is not None:
        return httpx.Response(status, content=content)
    return httpx.Response(
        status,
        json={"success": True, "id": "search-1", "data": {"web": []}},
    )


async def test_cloud_executor_uses_fixed_origin_and_bearer_header() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["origin"] = str(request.url.copy_with(path="", query=None)).rstrip("/")
        captured["authorization_matched"] = (
            request.headers.get("authorization") == f"Bearer {TEST_ONLY_CREDENTIAL}"
        )
        return metadata_response()

    executor = SecureFirecrawlExecutor(
        Settings(),
        cloud_transport=httpx.MockTransport(handler),
    )
    result = await executor.search(
        cloud_connection(),
        MetadataSearchRequest(query="site:example.com filetype:pdf", limit=1),
    )

    assert result.search_id == "search-1"
    assert captured == {
        "origin": "https://api.firecrawl.dev",
        "authorization_matched": True,
    }


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, ConnectionFailureCategory.INVALID_CREDENTIALS),
        (403, ConnectionFailureCategory.INVALID_CREDENTIALS),
        (408, ConnectionFailureCategory.TIMEOUT),
        (429, ConnectionFailureCategory.RATE_LIMITED),
        (500, ConnectionFailureCategory.SERVICE_UNAVAILABLE),
    ],
)
async def test_cloud_executor_maps_statuses_without_leaking_the_credential(
    status: int,
    category: ConnectionFailureCategory,
) -> None:
    executor = SecureFirecrawlExecutor(
        Settings(),
        cloud_transport=httpx.MockTransport(lambda request: metadata_response(status)),
    )

    with pytest.raises(ConnectionExecutionError) as raised:
        await executor.search(
            cloud_connection(),
            MetadataSearchRequest(query="report"),
        )

    assert raised.value.category is category
    assert TEST_ONLY_CREDENTIAL not in str(raised.value)


async def test_cloud_executor_rejects_redirects_and_oversized_responses() -> None:
    redirect = SecureFirecrawlExecutor(
        Settings(),
        cloud_transport=httpx.MockTransport(lambda request: metadata_response(302)),
    )
    with pytest.raises(ConnectionExecutionError) as redirected:
        await redirect.search(cloud_connection(), MetadataSearchRequest(query="report"))
    assert redirected.value.category is ConnectionFailureCategory.INCOMPATIBLE_RESPONSE

    oversized = SecureFirecrawlExecutor(
        Settings(firecrawl_response_limit_bytes=1024),
        cloud_transport=httpx.MockTransport(lambda request: metadata_response(200, b"x" * 1025)),
    )
    with pytest.raises(ConnectionExecutionError) as too_large:
        await oversized.search(cloud_connection(), MetadataSearchRequest(query="report"))
    assert too_large.value.category is ConnectionFailureCategory.RESPONSE_TOO_LARGE


async def test_cloud_executor_preserves_tls_failure_category() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLError("test double certificate failure")
        except ssl.SSLError as error:
            raise httpx.ConnectError("test double TLS failure", request=request) from error

    executor = SecureFirecrawlExecutor(
        Settings(),
        cloud_transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ConnectionExecutionError) as raised:
        await executor.search(cloud_connection(), MetadataSearchRequest(query="report"))

    assert raised.value.category is ConnectionFailureCategory.TLS_FAILURE


def remote_factory(
    recorder: list[RecordingRemoteTransport],
    *,
    failure: Exception | None = None,
) -> Callable[[NetworkPolicy, float, int], RemoteSearchTransport]:
    def create(
        policy: NetworkPolicy,
        timeout_seconds: float,
        response_limit_bytes: int,
    ) -> RemoteSearchTransport:
        transport = RecordingRemoteTransport(policy, failure=failure)
        recorder.append(transport)
        return transport

    return create


async def test_remote_executor_revalidates_dns_and_closes_each_transport() -> None:
    resolver = StaticResolver()
    transports: list[RecordingRemoteTransport] = []
    executor = SecureFirecrawlExecutor(
        Settings(),
        resolver=resolver,
        remote_transport_factory=remote_factory(transports),
    )

    result = await executor.search(
        remote_connection(),
        MetadataSearchRequest(query="report"),
    )

    assert result.search_id == "remote-search"
    assert transports[0].origin == "https://firecrawl.example"
    assert transports[0].authorization_matched is True
    assert transports[0].closed is True


@pytest.mark.parametrize(
    "answers",
    [
        ("93.184.216.34", "10.0.0.8"),
        ("::ffff:10.0.0.8",),
        ("10.0.0.8",),
    ],
)
async def test_remote_executor_blocks_any_unsafe_dns_answer(
    answers: tuple[str, ...],
) -> None:
    transports: list[RecordingRemoteTransport] = []
    executor = SecureFirecrawlExecutor(
        Settings(),
        resolver=StaticResolver(answers),
        remote_transport_factory=remote_factory(transports),
    )

    with pytest.raises(ConnectionExecutionError) as raised:
        await executor.search(remote_connection(), MetadataSearchRequest(query="report"))

    assert raised.value.category is ConnectionFailureCategory.BLOCKED_DESTINATION


@pytest.mark.parametrize(
    "origin",
    [
        "https://8.8.8.8",
        "https://firecrawl.example:8443",
    ],
)
async def test_remote_executor_rejects_literal_or_unapproved_origins(origin: str) -> None:
    executor = SecureFirecrawlExecutor(
        Settings(),
        resolver=StaticResolver(),
        remote_transport_factory=remote_factory([]),
    )

    with pytest.raises(ConnectionExecutionError) as raised:
        await executor.search(remote_connection(origin), MetadataSearchRequest(query="report"))

    assert raised.value.category is ConnectionFailureCategory.BLOCKED_DESTINATION


async def test_remote_executor_maps_dns_tls_and_timeout_failures() -> None:
    resolver = StaticResolver()
    resolver.raise_resolution_error = True
    dns_executor = SecureFirecrawlExecutor(
        Settings(),
        resolver=resolver,
        remote_transport_factory=remote_factory([]),
    )
    with pytest.raises(ConnectionExecutionError) as dns:
        await dns_executor.search(remote_connection(), MetadataSearchRequest(query="report"))
    assert dns.value.category is ConnectionFailureCategory.DNS_FAILURE

    for failure, category in (
        (ssl.SSLError("test double TLS failure"), ConnectionFailureCategory.TLS_FAILURE),
        (ReadTimeout(), ConnectionFailureCategory.TIMEOUT),
    ):
        executor = SecureFirecrawlExecutor(
            Settings(),
            resolver=StaticResolver(),
            remote_transport_factory=remote_factory([], failure=failure),
        )
        with pytest.raises(ConnectionExecutionError) as raised:
            await executor.search(remote_connection(), MetadataSearchRequest(query="report"))
        assert raised.value.category is category
