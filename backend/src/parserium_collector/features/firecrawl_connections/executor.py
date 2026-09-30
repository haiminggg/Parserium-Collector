import json
import ssl
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from httpcore import (
    AsyncConnectionPool,
    ConnectError,
    ConnectTimeout,
    NetworkError,
    PoolTimeout,
    ReadTimeout,
    Response,
    WriteTimeout,
)

from parserium_collector.adapters.firecrawl.client import (
    FirecrawlClient,
    parse_metadata_search_response,
)
from parserium_collector.adapters.firecrawl.contracts import (
    MetadataSearchRequest,
    MetadataSearchResult,
)
from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.acquisition.errors import (
    BlockedDestinationError,
    DestinationResolutionError,
)
from parserium_collector.features.acquisition.network_backend import (
    ValidatedNetworkBackend,
)
from parserium_collector.features.acquisition.network_policy import (
    AddressResolver,
    NetworkPolicy,
)
from parserium_collector.features.firecrawl_connections.endpoints import (
    FIRECRAWL_CLOUD_ORIGIN,
    normalize_remote_origin,
)
from parserium_collector.features.firecrawl_connections.errors import (
    RemoteEndpointRejectedError,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionFailureCategory,
    ConnectionType,
    ResolvedConnection,
)
from parserium_collector.settings import Settings

_USER_AGENT = b"Parserium-Collector/0.1"


class ConnectionExecutionError(RuntimeError):
    def __init__(self, category: ConnectionFailureCategory) -> None:
        super().__init__("The Firecrawl connection request failed.")
        self.category = category


class FirecrawlExecutor(Protocol):
    async def search(
        self,
        connection: ResolvedConnection,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult: ...


class RemoteSearchTransport(Protocol):
    async def search(
        self,
        origin: str,
        credential: str,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult: ...

    async def aclose(self) -> None: ...


class HttpcoreFirecrawlTransport:
    def __init__(
        self,
        policy: NetworkPolicy,
        timeout_seconds: float,
        response_limit_bytes: int,
    ) -> None:
        self._pool = AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=1,
            retries=0,
            network_backend=ValidatedNetworkBackend(policy=policy),
        )
        self._response_limit_bytes = response_limit_bytes
        self._extensions: dict[str, Any] = {
            "timeout": {
                "connect": timeout_seconds,
                "read": timeout_seconds,
                "write": timeout_seconds,
                "pool": timeout_seconds,
            }
        }

    async def search(
        self,
        origin: str,
        credential: str,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        body = json.dumps(
            request.firecrawl_body(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = (
            (b"accept", b"application/json"),
            (b"authorization", b"Bearer " + credential.encode("utf-8")),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"content-type", b"application/json"),
            (b"user-agent", _USER_AGENT),
        )
        async with self._pool.stream(
            "POST",
            f"{origin}/v2/search",
            headers=headers,
            content=body,
            extensions=self._extensions,
        ) as response:
            if not 200 <= response.status < 300:
                return parse_metadata_search_response(response.status, b"")
            content = await self._bounded_content(response)
        return parse_metadata_search_response(response.status, content)

    async def _bounded_content(self, response: Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_stream():
            size += len(chunk)
            if size > self._response_limit_bytes:
                raise FirecrawlAdapterError(code="response_too_large", retryable=False)
            chunks.append(chunk)
        return b"".join(chunks)

    async def aclose(self) -> None:
        await self._pool.aclose()


RemoteTransportFactory = Callable[
    [NetworkPolicy, float, int],
    RemoteSearchTransport,
]


class SecureFirecrawlExecutor:
    def __init__(
        self,
        settings: Settings,
        *,
        resolver: AddressResolver | None = None,
        cloud_transport: httpx.AsyncBaseTransport | None = None,
        remote_transport_factory: RemoteTransportFactory | None = None,
    ) -> None:
        self._settings = settings
        self._resolver = resolver
        self._cloud_transport = cloud_transport
        self._remote_transport_factory = remote_transport_factory or self._create_remote_transport

    async def search(
        self,
        connection: ResolvedConnection,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        try:
            if connection.connection_type is ConnectionType.CLOUD:
                return await self._search_cloud(connection, request)
            if connection.connection_type is ConnectionType.REMOTE:
                return await self._search_remote(connection, request)
            raise ConnectionExecutionError(ConnectionFailureCategory.INCOMPATIBLE_RESPONSE)
        except ConnectionExecutionError:
            raise
        except FirecrawlAdapterError as error:
            raise ConnectionExecutionError(self._adapter_category(error.code)) from None
        except DestinationResolutionError:
            raise ConnectionExecutionError(ConnectionFailureCategory.DNS_FAILURE) from None
        except (BlockedDestinationError, RemoteEndpointRejectedError):
            raise ConnectionExecutionError(ConnectionFailureCategory.BLOCKED_DESTINATION) from None
        except (ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout, TimeoutError):
            raise ConnectionExecutionError(ConnectionFailureCategory.TIMEOUT) from None
        except ssl.SSLError:
            raise ConnectionExecutionError(ConnectionFailureCategory.TLS_FAILURE) from None
        except ConnectError as error:
            category = (
                ConnectionFailureCategory.TLS_FAILURE
                if self._exception_chain_contains(error, ssl.SSLError)
                else ConnectionFailureCategory.SERVICE_UNAVAILABLE
            )
            raise ConnectionExecutionError(category) from None
        except NetworkError:
            raise ConnectionExecutionError(ConnectionFailureCategory.SERVICE_UNAVAILABLE) from None

    async def _search_cloud(
        self,
        connection: ResolvedConnection,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        async with httpx.AsyncClient(
            base_url=FIRECRAWL_CLOUD_ORIGIN,
            headers={"Authorization": f"Bearer {connection.credential}"},
            timeout=self._settings.firecrawl_validation_timeout_seconds,
            follow_redirects=False,
            transport=self._cloud_transport,
        ) as http_client:
            return await FirecrawlClient(
                http_client,
                response_limit_bytes=self._settings.firecrawl_response_limit_bytes,
            ).probe_metadata_search(request)

    async def _search_remote(
        self,
        connection: ResolvedConnection,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        if connection.normalized_base_url is None:
            raise RemoteEndpointRejectedError
        origin = normalize_remote_origin(
            connection.normalized_base_url,
            allowed_ports=self._settings.firecrawl_remote_allowed_ports,
        )
        policy = NetworkPolicy(
            resolver=self._resolver,
            allowed_public_ports=self._settings.firecrawl_remote_allowed_ports,
            private_allowlist=self._settings.firecrawl_remote_private_allowlist,
        )
        transport = self._remote_transport_factory(
            policy,
            self._settings.firecrawl_validation_timeout_seconds,
            self._settings.firecrawl_response_limit_bytes,
        )
        try:
            return await transport.search(origin, connection.credential, request)
        finally:
            await transport.aclose()

    @staticmethod
    def _create_remote_transport(
        policy: NetworkPolicy,
        timeout_seconds: float,
        response_limit_bytes: int,
    ) -> RemoteSearchTransport:
        return HttpcoreFirecrawlTransport(
            policy,
            timeout_seconds,
            response_limit_bytes,
        )

    @staticmethod
    def _adapter_category(code: str) -> ConnectionFailureCategory:
        if code in {"http_401", "http_403"}:
            return ConnectionFailureCategory.INVALID_CREDENTIALS
        if code == "http_429":
            return ConnectionFailureCategory.RATE_LIMITED
        if code in {"http_408", "timeout"}:
            return ConnectionFailureCategory.TIMEOUT
        if code == "response_too_large":
            return ConnectionFailureCategory.RESPONSE_TOO_LARGE
        if code == "tls_failure":
            return ConnectionFailureCategory.TLS_FAILURE
        if code in {
            "compatibility_mismatch",
            "invalid_json",
            "invalid_status",
            "redirect_response",
            "schema_mismatch",
            "unsuccessful_response",
        }:
            return ConnectionFailureCategory.INCOMPATIBLE_RESPONSE
        return ConnectionFailureCategory.SERVICE_UNAVAILABLE

    @staticmethod
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
