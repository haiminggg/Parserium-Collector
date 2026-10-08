import ssl
from collections.abc import Iterable
from typing import Any

import pytest
from httpcore import (
    SOCKET_OPTION,
    AsyncNetworkBackend,
    AsyncNetworkStream,
)

from parserium_collector.features.acquisition.errors import BlockedDestinationError
from parserium_collector.features.acquisition.network_backend import (
    ValidatedNetworkBackend,
)
from parserium_collector.features.acquisition.network_policy import NetworkPolicy


class StaticResolver:
    def __init__(self, address: str) -> None:
        self.address = address

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        return (self.address,)


class RecordingStream(AsyncNetworkStream):
    def __init__(self) -> None:
        self.tls_server_hostnames: list[str | None] = []

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> AsyncNetworkStream:
        self.tls_server_hostnames.append(server_hostname)
        return self

    def get_extra_info(self, info: str) -> Any:
        return None


class RecordingBackend(AsyncNetworkBackend):
    def __init__(self) -> None:
        self.stream = RecordingStream()
        self.connect_calls: list[
            tuple[str, int, float | None, str | None, Iterable[SOCKET_OPTION] | None]
        ] = []
        self.sleep_calls: list[float] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        self.connect_calls.append((host, port, timeout, local_address, socket_options))
        return self.stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        raise AssertionError("Unix socket delegation is prohibited")

    async def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)


@pytest.mark.parametrize(
    ("resolved_address", "expected_host"),
    (
        ("93.184.216.34", "93.184.216.34"),
        ("2606:2800:220:1:248:1893:25c8:1946", "2606:2800:220:1:248:1893:25c8:1946"),
    ),
)
async def test_connect_tcp_pins_an_approved_ip_and_preserves_tls_hostname(
    resolved_address: str,
    expected_host: str,
) -> None:
    recording = RecordingBackend()
    policy = NetworkPolicy(
        resolver=StaticResolver(resolved_address),
        allowed_public_ports=(80, 443),
        private_allowlist=(),
    )
    backend = ValidatedNetworkBackend(policy=policy, backend=recording)

    stream = await backend.connect_tcp(
        "secure.example",
        443,
        timeout=5.0,
        local_address="0.0.0.0",
        socket_options=(),
    )
    await stream.start_tls(
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        server_hostname=expected_host,
        timeout=5.0,
    )

    assert recording.connect_calls == [
        (expected_host, 443, 5.0, "0.0.0.0", ()),
    ]
    assert recording.stream.tls_server_hostnames == ["secure.example"]


async def test_sleep_is_delegated() -> None:
    recording = RecordingBackend()
    backend = ValidatedNetworkBackend(
        policy=NetworkPolicy(
            resolver=StaticResolver("93.184.216.34"),
            allowed_public_ports=(80, 443),
            private_allowlist=(),
        ),
        backend=recording,
    )

    await backend.sleep(0.25)

    assert recording.sleep_calls == [0.25]


async def test_connection_revalidates_dns_after_url_approval() -> None:
    resolver = StaticResolver("93.184.216.34")
    policy = NetworkPolicy(
        resolver=resolver,
        allowed_public_ports=(80, 443),
        private_allowlist=(),
    )
    recording = RecordingBackend()
    backend = ValidatedNetworkBackend(policy=policy, backend=recording)
    await policy.approve_url("https://secure.example/report.pdf")

    resolver.address = "127.0.0.1"

    with pytest.raises(BlockedDestinationError):
        await backend.connect_tcp("secure.example", 443)
    assert recording.connect_calls == []


async def test_unix_sockets_are_rejected_without_delegation() -> None:
    backend = ValidatedNetworkBackend(
        policy=NetworkPolicy(
            resolver=StaticResolver("93.184.216.34"),
            allowed_public_ports=(80, 443),
            private_allowlist=(),
        ),
        backend=RecordingBackend(),
    )

    with pytest.raises(BlockedDestinationError):
        await backend.connect_unix_socket("/var/run/internal.sock")
