import hashlib
from pathlib import Path
from typing import Any

import pytest
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError, EndpointConnectionError

from parserium_collector.features.storage.errors import (
    ArtifactConfigurationError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStorageUnavailableError,
)
from parserium_collector.features.storage.s3 import S3ArtifactStore

from .contract import DOCUMENT_KEY, PDF_CONTENT, assert_artifact_store_contract


class FakeStreamingBody:
    """Minimal test double for Botocore's synchronous streaming body."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self._offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._content) - self._offset
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    """In-memory test double limited to the S3 calls used by the adapter."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.upload_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, str]] = []
        self.get_calls: list[dict[str, str]] = []
        self.delete_calls: list[dict[str, str]] = []
        self.presign_calls: list[dict[str, Any]] = []
        self.next_error: Exception | None = None
        self.last_body: FakeStreamingBody | None = None

    def _raise_next(self) -> None:
        if self.next_error is not None:
            error = self.next_error
            self.next_error = None
            raise error

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        ExtraArgs: dict[str, Any],
        Config: TransferConfig,
    ) -> None:
        self._raise_next()
        content = Path(filename).read_bytes()
        self.upload_calls.append(
            {
                "Filename": filename,
                "Bucket": bucket,
                "Key": key,
                "ExtraArgs": ExtraArgs,
                "Config": Config,
            }
        )
        self.objects[key] = {
            "Body": content,
            "ContentLength": len(content),
            "ContentType": ExtraArgs["ContentType"],
            "Metadata": ExtraArgs["Metadata"],
        }

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self._raise_next()
        self.head_calls.append({"Bucket": Bucket, "Key": Key})
        if Key not in self.objects:
            raise client_error("NoSuchKey", 404, "HeadObject")
        return {name: value for name, value in self.objects[Key].items() if name != "Body"}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self._raise_next()
        self.get_calls.append({"Bucket": Bucket, "Key": Key})
        if Key not in self.objects:
            raise client_error("NoSuchKey", 404, "GetObject")
        stored = self.objects[Key]
        body = FakeStreamingBody(stored["Body"])
        self.last_body = body
        return {
            "Body": body,
            "ContentLength": stored["ContentLength"],
            "ContentType": stored["ContentType"],
            "Metadata": stored["Metadata"],
        }

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self._raise_next()
        self.delete_calls.append({"Bucket": Bucket, "Key": Key})
        self.objects.pop(Key, None)
        return {}

    def generate_presigned_url(self, **kwargs: Any) -> str:
        self._raise_next()
        self.presign_calls.append(kwargs)
        return "https://signed.invalid/private-value"


def client_error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "provider detail must stay private"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


def s3_store(client: FakeS3Client | None = None) -> tuple[S3ArtifactStore, FakeS3Client]:
    resolved = client or FakeS3Client()
    return (
        S3ArtifactStore(client=resolved, bucket="parserium", max_concurrency=2),
        resolved,
    )


async def test_s3_adapter_contract(tmp_path: Path) -> None:
    store, _ = s3_store()

    await assert_artifact_store_contract(store, tmp_path)


async def test_upload_uses_private_metadata_and_bounded_transfer(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_CONTENT)
    digest = hashlib.sha256(PDF_CONTENT).hexdigest()
    store, client = s3_store()

    await store.put_file(
        DOCUMENT_KEY,
        source,
        media_type="application/pdf",
        sha256=digest,
        size_bytes=len(PDF_CONTENT),
    )

    call = client.upload_calls[0]
    assert call["Bucket"] == "parserium"
    assert call["Key"] == DOCUMENT_KEY
    assert call["ExtraArgs"] == {
        "ContentType": "application/pdf",
        "Metadata": {
            "parserium-sha256": digest,
            "parserium-size": str(len(PDF_CONTENT)),
        },
    }
    transfer = call["Config"]
    assert transfer.multipart_threshold == 8 * 1024 * 1024
    assert transfer.multipart_chunksize == 8 * 1024 * 1024
    assert transfer.max_request_concurrency == 1
    assert transfer.use_threads is False
    assert client.head_calls == [{"Bucket": "parserium", "Key": DOCUMENT_KEY}]


async def test_upload_rejects_provider_metadata_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_CONTENT)
    digest = hashlib.sha256(PDF_CONTENT).hexdigest()
    store, client = s3_store()

    original_head = client.head_object

    def mismatched_head(*, Bucket: str, Key: str) -> dict[str, Any]:
        response = original_head(Bucket=Bucket, Key=Key)
        response["Metadata"] = {"parserium-sha256": "0" * 64, "parserium-size": "1"}
        return response

    client.head_object = mismatched_head  # type: ignore[method-assign]

    with pytest.raises(ArtifactIntegrityError):
        await store.put_file(
            DOCUMENT_KEY,
            source,
            media_type="application/pdf",
            sha256=digest,
            size_bytes=len(PDF_CONTENT),
        )


async def test_presign_get_binds_private_response_headers() -> None:
    store, client = s3_store()

    url = await store.presign_get(
        DOCUMENT_KEY,
        filename="annual report.pdf",
        media_type="application/pdf",
        ttl_seconds=60,
    )

    assert url == "https://signed.invalid/private-value"
    assert client.presign_calls == [
        {
            "ClientMethod": "get_object",
            "Params": {
                "Bucket": "parserium",
                "Key": DOCUMENT_KEY,
                "ResponseContentDisposition": 'attachment; filename="annual report.pdf"',
                "ResponseContentType": "application/pdf",
                "ResponseCacheControl": "private, no-store",
            },
            "ExpiresIn": 60,
            "HttpMethod": "GET",
        }
    ]


async def test_presign_uses_the_signing_client_only() -> None:
    operational = FakeS3Client()
    signing = FakeS3Client()
    store = S3ArtifactStore(
        client=operational,
        signing_client=signing,
        bucket="parserium",
        max_concurrency=2,
    )

    await store.presign_get(
        DOCUMENT_KEY,
        filename="report.pdf",
        media_type="application/pdf",
        ttl_seconds=60,
    )

    assert operational.presign_calls == []
    assert len(signing.presign_calls) == 1


@pytest.mark.parametrize("ttl", [29, 301])
async def test_presign_get_rejects_an_unbounded_lifetime(ttl: int) -> None:
    store, _ = s3_store()

    with pytest.raises(ArtifactConfigurationError, match="lifetime"):
        await store.presign_get(
            DOCUMENT_KEY,
            filename="report.pdf",
            media_type="application/pdf",
            ttl_seconds=ttl,
        )


async def test_reader_closes_the_provider_body(tmp_path: Path) -> None:
    store, client = s3_store()
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_CONTENT)
    await store.put_file(
        DOCUMENT_KEY,
        source,
        media_type="application/pdf",
        sha256=hashlib.sha256(PDF_CONTENT).hexdigest(),
        size_bytes=len(PDF_CONTENT),
    )

    async with store.open_reader(DOCUMENT_KEY) as reader:
        assert await reader.read() == PDF_CONTENT

    assert client.last_body is not None
    assert client.last_body.closed is True


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (client_error("NoSuchKey", 404, "HeadObject"), ArtifactNotFoundError),
        (client_error("AccessDenied", 403, "HeadObject"), ArtifactConfigurationError),
        (client_error("SlowDown", 503, "HeadObject"), ArtifactStorageUnavailableError),
        (
            EndpointConnectionError(endpoint_url="https://private-endpoint.invalid"),
            ArtifactStorageUnavailableError,
        ),
    ],
)
async def test_provider_failures_are_normalized_without_private_detail(
    error: Exception,
    expected: type[Exception],
) -> None:
    store, client = s3_store()
    client.next_error = error

    with pytest.raises(expected) as captured:
        await store.stat(DOCUMENT_KEY)

    message = str(captured.value)
    assert "provider detail" not in message
    assert "private-endpoint" not in message
    assert DOCUMENT_KEY not in message
