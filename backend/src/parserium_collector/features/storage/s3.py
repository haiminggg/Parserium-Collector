from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar

import anyio
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ParamValidationError,
    PartialCredentialsError,
    ReadTimeoutError,
    SSLError,
)
from botocore.response import StreamingBody

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

from parserium_collector.features.storage.access import content_disposition
from parserium_collector.features.storage.errors import (
    ArtifactConfigurationError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStorageError,
    ArtifactStorageUnavailableError,
)
from parserium_collector.features.storage.keys import validate_storage_key
from parserium_collector.features.storage.models import StoredObjectMetadata

T = TypeVar("T")
MULTIPART_SIZE = 8 * 1024 * 1024
NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
RETRYABLE_CODES = frozenset(
    {
        "InternalError",
        "RequestTimeout",
        "RequestTimeoutException",
        "ServiceUnavailable",
        "SlowDown",
        "Throttling",
        "ThrottlingException",
    }
)
CONFIGURATION_CODES = frozenset(
    {
        "AccessDenied",
        "AuthorizationHeaderMalformed",
        "InvalidAccessKeyId",
        "InvalidBucketName",
        "NoSuchBucket",
        "PermanentRedirect",
        "SignatureDoesNotMatch",
    }
)


class _S3Reader:
    def __init__(self, body: StreamingBody, limiter: anyio.CapacityLimiter) -> None:
        self._body = body
        self._limiter = limiter

    async def read(self, size: int = -1) -> bytes:
        async with self._limiter:
            try:
                return await anyio.to_thread.run_sync(self._body.read, size)
            except Exception as error:
                _raise_normalized(error, "read")

    async def close(self) -> None:
        async with self._limiter:
            await anyio.to_thread.run_sync(self._body.close)


class S3ArtifactStore:
    def __init__(
        self,
        *,
        client: S3Client,
        signing_client: S3Client | None = None,
        bucket: str,
        max_concurrency: int,
    ) -> None:
        if not bucket.strip():
            raise ValueError("The S3 bucket must not be empty.")
        if max_concurrency < 1:
            raise ValueError("S3 concurrency must be positive.")
        self.client = client
        self.signing_client = signing_client or client
        self._bucket = bucket
        self._limiter = anyio.CapacityLimiter(max_concurrency)
        self._transfer_config = TransferConfig(
            multipart_threshold=MULTIPART_SIZE,
            multipart_chunksize=MULTIPART_SIZE,
            max_concurrency=1,
            use_threads=False,
        )

    async def put_file(
        self,
        storage_key: str,
        source: Path,
        *,
        media_type: str,
        sha256: str,
        size_bytes: int,
    ) -> StoredObjectMetadata:
        validate_storage_key(storage_key)
        try:
            expected = StoredObjectMetadata(
                storage_key=storage_key,
                media_type=media_type,
                size_bytes=size_bytes,
                sha256=sha256,
            )
        except ValueError as error:
            raise ArtifactIntegrityError("The expected artifact metadata is invalid.") from error
        await self._verify_source(source, expected)
        await self._call(
            partial(
                self.client.upload_file,
                str(source),
                self._bucket,
                storage_key,
                ExtraArgs={
                    "ContentType": media_type,
                    "Metadata": {
                        "parserium-sha256": sha256,
                        "parserium-size": str(size_bytes),
                    },
                },
                Config=self._transfer_config,
            ),
            "upload",
        )
        actual = await self.stat(storage_key)
        if actual != expected:
            raise ArtifactIntegrityError("The stored artifact metadata is invalid.")
        return actual

    async def stat(self, storage_key: str) -> StoredObjectMetadata:
        validate_storage_key(storage_key)
        response = await self._call(
            partial(self.client.head_object, Bucket=self._bucket, Key=storage_key),
            "metadata read",
        )
        return self._metadata_from_response(storage_key, response)

    @asynccontextmanager
    async def open_reader(self, storage_key: str) -> AsyncIterator[_S3Reader]:
        validate_storage_key(storage_key)
        response = await self._call(
            partial(self.client.get_object, Bucket=self._bucket, Key=storage_key),
            "read",
        )
        body = response.get("Body")
        if body is None or not hasattr(body, "read") or not hasattr(body, "close"):
            raise ArtifactIntegrityError("The artifact response body is invalid.")
        reader = _S3Reader(body, self._limiter)
        try:
            yield reader
        finally:
            await reader.close()

    async def presign_get(
        self,
        storage_key: str,
        *,
        filename: str,
        media_type: str,
        ttl_seconds: int,
    ) -> str:
        validate_storage_key(storage_key)
        if not 30 <= ttl_seconds <= 300:
            raise ArtifactConfigurationError("The signed download lifetime is invalid.")
        response_disposition = content_disposition(filename)
        result = await self._call(
            partial(
                self.signing_client.generate_presigned_url,
                ClientMethod="get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": storage_key,
                    "ResponseContentDisposition": response_disposition,
                    "ResponseContentType": media_type,
                    "ResponseCacheControl": "private, no-store",
                },
                ExpiresIn=ttl_seconds,
                HttpMethod="GET",
            ),
            "signing",
        )
        if not isinstance(result, str) or not result:
            raise ArtifactIntegrityError("The signed download response is invalid.")
        return result

    async def delete(self, storage_key: str) -> None:
        validate_storage_key(storage_key)
        await self._call(
            partial(self.client.delete_object, Bucket=self._bucket, Key=storage_key),
            "deletion",
        )

    async def probe(self, probe_key: str) -> None:
        validate_storage_key(probe_key)
        await self.delete(probe_key)
        content = secrets.token_bytes(32)
        digest = hashlib.sha256(content).hexdigest()
        descriptor, raw_path = tempfile.mkstemp(prefix="parserium-storage-probe-")
        source = Path(raw_path)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            await self.put_file(
                probe_key,
                source,
                media_type="application/octet-stream",
                sha256=digest,
                size_bytes=len(content),
            )
            chunks: list[bytes] = []
            async with self.open_reader(probe_key) as reader:
                while chunk := await reader.read(64 * 1024):
                    chunks.append(chunk)
            if b"".join(chunks) != content:
                raise ArtifactIntegrityError("The storage readiness probe content is invalid.")
        finally:
            await anyio.to_thread.run_sync(source.unlink, True)
            await self.delete(probe_key)

    async def _verify_source(
        self,
        source: Path,
        expected: StoredObjectMetadata,
    ) -> None:
        try:
            digest, size_bytes = await anyio.to_thread.run_sync(_verify_source_file, source)
        except ArtifactStorageError:
            raise
        except OSError as error:
            raise ArtifactIntegrityError("The artifact upload source is unavailable.") from error
        if digest != expected.sha256 or size_bytes != expected.size_bytes:
            raise ArtifactIntegrityError("The artifact upload metadata does not match its bytes.")

    async def _call(self, operation: Callable[[], T], name: str) -> T:
        async with self._limiter:
            try:
                return await anyio.to_thread.run_sync(operation)
            except Exception as error:
                _raise_normalized(error, name)

    @staticmethod
    def _metadata_from_response(
        storage_key: str,
        response: Mapping[str, Any],
    ) -> StoredObjectMetadata:
        metadata = response.get("Metadata")
        if not isinstance(metadata, Mapping):
            raise ArtifactIntegrityError("The stored artifact metadata is invalid.")
        sha256 = metadata.get("parserium-sha256")
        stored_size = metadata.get("parserium-size")
        content_length = response.get("ContentLength")
        media_type = response.get("ContentType")
        if (
            not isinstance(sha256, str)
            or not isinstance(stored_size, str)
            or not isinstance(content_length, int)
            or not isinstance(media_type, str)
        ):
            raise ArtifactIntegrityError("The stored artifact metadata is invalid.")
        try:
            metadata_size = int(stored_size)
            result = StoredObjectMetadata(
                storage_key=storage_key,
                media_type=media_type,
                size_bytes=content_length,
                sha256=sha256,
            )
        except ValueError as error:
            raise ArtifactIntegrityError("The stored artifact metadata is invalid.") from error
        if metadata_size != content_length:
            raise ArtifactIntegrityError("The stored artifact metadata is invalid.")
        return result


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def _verify_source_file(source: Path) -> tuple[str, int]:
    if source.is_symlink():
        raise ArtifactIntegrityError("The artifact upload source is not a regular file.")
    resolved = source.resolve(strict=True)
    if not resolved.is_file():
        raise ArtifactIntegrityError("The artifact upload source is not a regular file.")
    return _hash_file(resolved)


def _raise_normalized(error: Exception, operation: str) -> NoReturn:
    if isinstance(error, ClientError):
        response = error.response
        error_detail = response.get("Error", {})
        code = str(error_detail.get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in NOT_FOUND_CODES or status == 404:
            raise ArtifactNotFoundError(f"S3 artifact {operation} found no object.") from error
        if code in RETRYABLE_CODES or (isinstance(status, int) and status >= 500):
            raise ArtifactStorageUnavailableError(
                f"S3 artifact {operation} is temporarily unavailable."
            ) from error
        if code in CONFIGURATION_CODES or status in {400, 401, 403}:
            raise ArtifactConfigurationError(
                f"S3 artifact {operation} is not authorized or configured."
            ) from error
        raise ArtifactConfigurationError(f"S3 artifact {operation} failed safely.") from error
    if isinstance(
        error,
        (ConnectTimeoutError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError),
    ):
        raise ArtifactStorageUnavailableError(
            f"S3 artifact {operation} is temporarily unavailable."
        ) from error
    if isinstance(
        error,
        (NoCredentialsError, ParamValidationError, PartialCredentialsError, SSLError),
    ):
        raise ArtifactConfigurationError(f"S3 artifact {operation} is not configured.") from error
    if isinstance(error, BotoCoreError):
        raise ArtifactStorageUnavailableError(
            f"S3 artifact {operation} is temporarily unavailable."
        ) from error
    raise ArtifactStorageUnavailableError(
        f"S3 artifact {operation} is temporarily unavailable."
    ) from error
