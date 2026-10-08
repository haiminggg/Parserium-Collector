import ssl
from collections.abc import Iterable
from typing import Any

from httpcore import (
    SOCKET_OPTION,
    AnyIOBackend,
    AsyncNetworkBackend,
    AsyncNetworkStream,
)

from parserium_collector.features.acquisition.errors import BlockedDestinationError
from parserium_collector.features.acquisition.network_policy import NetworkPolicy


class PinnedNetworkStream(AsyncNetworkStream):
    def __init__(self, stream: AsyncNetworkStream, server_hostname: str) -> None:
        self._stream = stream
        self._server_hostname = server_hostname

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return await self._stream.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self._stream.write(buffer, timeout)

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> AsyncNetworkStream:
        tls_stream = await self._stream.start_tls(
            ssl_context,
            server_hostname=self._server_hostname,
            timeout=timeout,
        )
        return PinnedNetworkStream(tls_stream, self._server_hostname)

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class ValidatedNetworkBackend(AsyncNetworkBackend):
    def __init__(
        self,
        *,
        policy: NetworkPolicy,
        backend: AsyncNetworkBackend | None = None,
    ) -> None:
        self._policy = policy
        self._backend = backend or AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        endpoint = await self._policy.approve_connection(host, port)
        stream = await self._backend.connect_tcp(
            str(endpoint.addresses[0]),
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        return PinnedNetworkStream(stream, endpoint.hostname)

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        raise BlockedDestinationError("Unix socket destinations are not allowed.")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)
