import hashlib
import ssl
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import anyio
import pytest
from httpcore import ConnectError, ConnectTimeout, ReadTimeout, Response

from parserium_collector.features.acquisition.downloader import BoundedDownloader
from parserium_collector.features.acquisition.errors import (
    BlockedDestinationError,
    DownloadTimeoutError,
    HttpPermanentError,
    HttpRetryableError,
    RedirectLimitError,
    TlsFailureError,
    TooLargeError,
)
from parserium_collector.features.acquisition.network_policy import NetworkPolicy


class PublicResolver:
    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)


@dataclass(frozen=True)
class ResponseSpec:
    status: int = 200
    headers: tuple[tuple[bytes, bytes], ...] = ()
    chunks: tuple[bytes, ...] = ()
    error: Exception | None = None
    wait_forever: bool = False


@dataclass
class FakeTransport:
    responses: list[ResponseSpec]
    requests: list[tuple[str, Sequence[tuple[bytes, bytes]], dict[str, Any]]] = field(
        default_factory=list
    )
    closed: bool = False

    @asynccontextmanager
    async def stream(
        self,
        url: str,
        headers: Sequence[tuple[bytes, bytes]],
        extensions: dict[str, Any],
    ) -> AsyncIterator[Response]:
        self.requests.append((url, headers, extensions))
        specification = self.responses.pop(0)
        if specification.error is not None:
            raise specification.error
        if specification.wait_forever:
            await anyio.sleep_forever()

        async def body() -> AsyncIterator[bytes]:
            for chunk in specification.chunks:
                yield chunk

        response = Response(
            specification.status,
            headers=specification.headers,
            content=body(),
        )
        try:
            yield response
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class MemorySink:
    chunks: list[bytes] = field(default_factory=list)

    async def write(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    @property
    def content(self) -> bytes:
        return b"".join(self.chunks)


def downloader_for(
    responses: list[ResponseSpec],
    *,
    max_bytes: int = 10,
    max_redirects: int = 5,
    total_timeout: float = 10.0,
) -> tuple[BoundedDownloader, FakeTransport]:
    transport = FakeTransport(responses)
    downloader = BoundedDownloader(
        policy=NetworkPolicy(
            resolver=PublicResolver(),
            allowed_public_ports=(80, 443),
            private_allowlist=(),
        ),
        max_bytes=max_bytes,
        connect_timeout=2.0,
        read_timeout=3.0,
        total_timeout=total_timeout,
        max_redirects=max_redirects,
        transport=transport,
    )
    return downloader, transport


async def test_streams_without_content_length_and_reports_progress() -> None:
    downloader, transport = downloader_for(
        [
            ResponseSpec(
                headers=((b"content-type", b"application/pdf; charset=binary"),),
                chunks=(b"abc", b"def"),
            )
        ],
        max_bytes=6,
    )
    sink = MemorySink()
    progress: list[tuple[int, int | None]] = []

    async def record_progress(downloaded: int, total: int | None) -> None:
        progress.append((downloaded, total))

    result = await downloader.download(
        "https://Example.com./report.pdf",
        sink,
        progress=record_progress,
    )

    assert sink.content == b"abcdef"
    assert result.size_bytes == 6
    assert result.sha256 == hashlib.sha256(b"abcdef").hexdigest()
    assert result.media_type == "application/pdf"
    assert result.final_url == "https://example.com/report.pdf"
    assert progress == [(3, None), (6, None)]
    assert transport.requests[0][1] == ((b"user-agent", b"Parserium-Collector/0.1"),)
    assert transport.requests[0][2]["timeout"] == {
        "connect": 2.0,
        "read": 3.0,
        "write": 3.0,
        "pool": 2.0,
    }


async def test_exact_content_limit_succeeds() -> None:
    downloader, _ = downloader_for(
        [ResponseSpec(headers=((b"content-length", b"6"),), chunks=(b"abcdef",))],
        max_bytes=6,
    )
    sink = MemorySink()

    result = await downloader.download("https://example.com/report.pdf", sink)

    assert result.size_bytes == 6
    assert result.content_length == 6


async def test_declared_content_length_over_limit_fails_before_writing() -> None:
    downloader, _ = downloader_for(
        [ResponseSpec(headers=((b"content-length", b"7"),), chunks=(b"abcdefg",))],
        max_bytes=6,
    )
    sink = MemorySink()

    with pytest.raises(TooLargeError):
        await downloader.download("https://example.com/report.pdf", sink)

    assert sink.content == b""


async def test_false_small_content_length_cannot_bypass_stream_limit() -> None:
    downloader, _ = downloader_for(
        [ResponseSpec(headers=((b"content-length", b"1"),), chunks=(b"abcdefg",))],
        max_bytes=6,
    )
    sink = MemorySink()

    with pytest.raises(TooLargeError):
        await downloader.download("https://example.com/report.pdf", sink)

    assert sink.content == b""


async def test_relative_redirect_is_followed_and_each_hop_is_revalidated() -> None:
    downloader, transport = downloader_for(
        [
            ResponseSpec(status=302, headers=((b"location", b"/final.pdf"),)),
            ResponseSpec(chunks=(b"pdf",)),
        ]
    )
    sink = MemorySink()

    result = await downloader.download("https://example.com/start.pdf", sink)

    assert result.final_url == "https://example.com/final.pdf"
    assert [request[0] for request in transport.requests] == [
        "https://example.com/start.pdf",
        "https://example.com/final.pdf",
    ]


async def test_five_redirects_succeed() -> None:
    responses = [
        ResponseSpec(status=302, headers=((b"location", f"/{index + 1}.pdf".encode()),))
        for index in range(5)
    ]
    responses.append(ResponseSpec(chunks=(b"pdf",)))
    downloader, transport = downloader_for(responses)

    result = await downloader.download("https://example.com/0.pdf", MemorySink())

    assert result.final_url == "https://example.com/5.pdf"
    assert len(transport.requests) == 6


async def test_sixth_redirect_is_rejected() -> None:
    responses = [
        ResponseSpec(status=302, headers=((b"location", f"/{index + 1}.pdf".encode()),))
        for index in range(6)
    ]
    downloader, transport = downloader_for(responses)

    with pytest.raises(RedirectLimitError):
        await downloader.download("https://example.com/0.pdf", MemorySink())

    assert len(transport.requests) == 6


async def test_https_redirect_cannot_downgrade_to_http() -> None:
    downloader, transport = downloader_for(
        [
            ResponseSpec(
                status=302,
                headers=((b"location", b"http://example.com/final.pdf"),),
            )
        ]
    )

    with pytest.raises(BlockedDestinationError):
        await downloader.download("https://example.com/start.pdf", MemorySink())

    assert len(transport.requests) == 1


async def test_redirect_to_prohibited_target_is_blocked_before_request() -> None:
    downloader, transport = downloader_for(
        [
            ResponseSpec(
                status=302,
                headers=((b"location", b"http://127.0.0.1/internal.pdf"),),
            )
        ]
    )

    with pytest.raises(BlockedDestinationError):
        await downloader.download("https://example.com/start.pdf", MemorySink())

    assert len(transport.requests) == 1


@pytest.mark.parametrize("status", (429, 500, 503))
async def test_retryable_http_statuses_are_classified(status: int) -> None:
    downloader, _ = downloader_for([ResponseSpec(status=status)])

    with pytest.raises(HttpRetryableError) as error:
        await downloader.download("https://example.com/report.pdf", MemorySink())

    assert error.value.status == status


async def test_permanent_http_status_is_classified() -> None:
    downloader, _ = downloader_for([ResponseSpec(status=404)])

    with pytest.raises(HttpPermanentError) as error:
        await downloader.download("https://example.com/report.pdf", MemorySink())

    assert error.value.status == 404


@pytest.mark.parametrize("transport_error", (ConnectTimeout(), ReadTimeout()))
async def test_connect_and_read_timeouts_are_classified(transport_error: Exception) -> None:
    downloader, _ = downloader_for([ResponseSpec(error=transport_error)])

    with pytest.raises(DownloadTimeoutError):
        await downloader.download("https://example.com/report.pdf", MemorySink())


async def test_total_timeout_is_classified() -> None:
    downloader, _ = downloader_for(
        [ResponseSpec(wait_forever=True)],
        total_timeout=0.01,
    )

    with pytest.raises(DownloadTimeoutError):
        await downloader.download("https://example.com/report.pdf", MemorySink())


async def test_tls_failure_is_classified_without_exposing_library_error() -> None:
    tls_cause = ssl.SSLCertVerificationError("certificate verify failed")
    transport_error = ConnectError("connection failed")
    transport_error.__cause__ = tls_cause
    downloader, _ = downloader_for([ResponseSpec(error=transport_error)])

    with pytest.raises(TlsFailureError):
        await downloader.download("https://example.com/report.pdf", MemorySink())


async def test_transport_can_be_closed() -> None:
    downloader, transport = downloader_for([])

    await downloader.aclose()

    assert transport.closed is True
