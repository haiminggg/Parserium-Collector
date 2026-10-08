import hashlib
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import anyio
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

from parserium_collector.features.acquisition.errors import (
    AcquisitionError,
    BlockedDestinationError,
    DownloadNetworkError,
    DownloadTimeoutError,
    HttpPermanentError,
    HttpRetryableError,
    InvalidResponseError,
    RedirectLimitError,
    TlsFailureError,
    TooLargeError,
)
from parserium_collector.features.acquisition.network_backend import (
    ValidatedNetworkBackend,
)
from parserium_collector.features.acquisition.network_policy import (
    ApprovedTarget,
    NetworkPolicy,
)

REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
RETRYABLE_STATUSES = frozenset((408, 429))
USER_AGENT = b"Parserium-Collector/0.1"


class AsyncByteSink(Protocol):
    async def write(self, chunk: bytes) -> None: ...


class StreamingTransport(Protocol):
    def stream(
        self,
        url: str,
        headers: Sequence[tuple[bytes, bytes]],
        extensions: dict[str, Any],
    ) -> AbstractAsyncContextManager[Response]: ...

    async def aclose(self) -> None: ...


ProgressCallback = Callable[[int, int | None], Awaitable[None]]


@dataclass(frozen=True)
class DownloadResult:
    final_url: str
    sha256: str
    size_bytes: int
    content_length: int | None
    media_type: str


class HttpcoreStreamingTransport:
    def __init__(self, policy: NetworkPolicy) -> None:
        self._pool = AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=1,
            retries=0,
            network_backend=ValidatedNetworkBackend(policy=policy),
        )

    @asynccontextmanager
    async def stream(
        self,
        url: str,
        headers: Sequence[tuple[bytes, bytes]],
        extensions: dict[str, Any],
    ) -> AsyncIterator[Response]:
        async with self._pool.stream(
            "GET",
            url,
            headers=headers,
            extensions=extensions,
        ) as response:
            yield response

    async def aclose(self) -> None:
        await self._pool.aclose()


class BoundedDownloader:
    def __init__(
        self,
        *,
        policy: NetworkPolicy,
        max_bytes: int,
        connect_timeout: float,
        read_timeout: float,
        total_timeout: float,
        max_redirects: int,
        transport: StreamingTransport | None = None,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("The download byte limit must be positive.")
        if min(connect_timeout, read_timeout, total_timeout) <= 0:
            raise ValueError("Download timeouts must be positive.")
        if max_redirects < 0:
            raise ValueError("The redirect limit cannot be negative.")
        self._policy = policy
        self._max_bytes = max_bytes
        self._total_timeout = total_timeout
        self._max_redirects = max_redirects
        self._extensions: dict[str, Any] = {
            "timeout": {
                "connect": connect_timeout,
                "read": read_timeout,
                "write": read_timeout,
                "pool": connect_timeout,
            }
        }
        self._transport = transport or HttpcoreStreamingTransport(policy)

    async def download(
        self,
        url: str,
        sink: AsyncByteSink,
        *,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        try:
            with anyio.fail_after(self._total_timeout):
                return await self._download(url, sink, progress)
        except AcquisitionError:
            raise
        except (ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout, TimeoutError) as error:
            raise DownloadTimeoutError("The document download timed out.") from error
        except ssl.SSLError as error:
            raise TlsFailureError("The document server TLS connection failed.") from error
        except ConnectError as error:
            if self._exception_chain_contains(error, ssl.SSLError):
                raise TlsFailureError("The document server TLS connection failed.") from error
            raise DownloadNetworkError("The document server connection failed.") from error
        except NetworkError as error:
            raise DownloadNetworkError("The document download failed.") from error

    async def _download(
        self,
        url: str,
        sink: AsyncByteSink,
        progress: ProgressCallback | None,
    ) -> DownloadResult:
        current_url = url
        redirects_followed = 0
        while True:
            target = await self._policy.approve_url(current_url)
            canonical_url = self._canonical_url(current_url, target)
            async with self._transport.stream(
                canonical_url,
                ((b"user-agent", USER_AGENT),),
                self._extensions,
            ) as response:
                if response.status in REDIRECT_STATUSES:
                    if redirects_followed >= self._max_redirects:
                        raise RedirectLimitError("The document URL redirected too many times.")
                    location = self._one_header(response, b"location")
                    if location is None:
                        raise InvalidResponseError(
                            "The redirect response did not include a location."
                        )
                    redirected_url = urljoin(canonical_url, self._decode_location(location))
                    redirected_scheme = urlsplit(redirected_url).scheme.lower()
                    if target.scheme == "https" and redirected_scheme == "http":
                        raise BlockedDestinationError(
                            "HTTPS document URLs cannot redirect to HTTP."
                        )
                    current_url = redirected_url
                    redirects_followed += 1
                    continue

                self._raise_for_status(response.status)
                content_length = self._content_length(response)
                if content_length is not None and content_length > self._max_bytes:
                    raise TooLargeError("The document exceeds the configured size limit.")
                media_type = self._media_type(response)
                digest = hashlib.sha256()
                downloaded = 0
                async for chunk in response.aiter_stream():
                    if not chunk:
                        continue
                    next_downloaded = downloaded + len(chunk)
                    if next_downloaded > self._max_bytes:
                        raise TooLargeError("The document exceeds the configured size limit.")
                    await sink.write(chunk)
                    digest.update(chunk)
                    downloaded = next_downloaded
                    if progress is not None:
                        await progress(downloaded, content_length)
                return DownloadResult(
                    final_url=canonical_url,
                    sha256=digest.hexdigest(),
                    size_bytes=downloaded,
                    content_length=content_length,
                    media_type=media_type,
                )

    async def aclose(self) -> None:
        await self._transport.aclose()

    @staticmethod
    def _canonical_url(url: str, target: ApprovedTarget) -> str:
        parts = urlsplit(url)
        hostname = f"[{target.hostname}]" if ":" in target.hostname else target.hostname
        default_port = 443 if target.scheme == "https" else 80
        netloc = hostname if target.port == default_port else f"{hostname}:{target.port}"
        return urlunsplit((target.scheme, netloc, parts.path, parts.query, ""))

    @staticmethod
    def _one_header(response: Response, name: bytes) -> bytes | None:
        values = [value for key, value in response.headers if key.lower() == name]
        if not values:
            return None
        if len(values) != 1:
            raise InvalidResponseError("The document server returned conflicting headers.")
        return values[0]

    @classmethod
    def _content_length(cls, response: Response) -> int | None:
        raw_value = cls._one_header(response, b"content-length")
        if raw_value is None:
            return None
        values = [part.strip() for part in raw_value.split(b",")]
        if not values or any(not value.isdigit() for value in values):
            raise InvalidResponseError("The document server returned an invalid length.")
        lengths = {int(value) for value in values}
        if len(lengths) != 1:
            raise InvalidResponseError("The document server returned conflicting lengths.")
        return lengths.pop()

    @classmethod
    def _media_type(cls, response: Response) -> str:
        raw_value = cls._one_header(response, b"content-type")
        if raw_value is None:
            return "application/octet-stream"
        media_type = raw_value.split(b";", maxsplit=1)[0].strip().lower()
        try:
            decoded = media_type.decode("ascii")
        except UnicodeDecodeError:
            return "application/octet-stream"
        return decoded or "application/octet-stream"

    @staticmethod
    def _decode_location(value: bytes) -> str:
        try:
            return value.decode("ascii")
        except UnicodeDecodeError as error:
            raise InvalidResponseError(
                "The document server returned an invalid redirect location."
            ) from error

    @staticmethod
    def _raise_for_status(status: int) -> None:
        if 200 <= status < 300:
            return
        if status in RETRYABLE_STATUSES or 500 <= status < 600:
            raise HttpRetryableError(status, "The document server returned a temporary error.")
        raise HttpPermanentError(status, "The document server rejected the request.")

    @staticmethod
    def _exception_chain_contains(error: BaseException, expected: type[BaseException]) -> bool:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            if isinstance(current, expected):
                return True
            seen.add(id(current))
            current = current.__cause__ or current.__context__
        return False
