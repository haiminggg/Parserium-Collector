import hashlib
import os
import ssl
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import anyio
import boto3
import httpx
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from mypy_boto3_s3 import S3Client

from parserium_collector.features.storage.errors import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStorageUnavailableError,
)
from parserium_collector.features.storage.keys import artifact_key, health_probe_key
from parserium_collector.features.storage.maintenance import ArtifactMaintenanceService
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactObjectState,
    ArtifactResourceKind,
)
from parserium_collector.features.storage.repository import ArtifactObjectRecord
from parserium_collector.features.storage.s3 import MULTIPART_SIZE, S3ArtifactStore


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required S3 integration setting {name} is missing.")
    return value


def read_secret(name: str) -> str:
    path = Path(required_environment(name))
    if not path.is_file():
        raise RuntimeError(f"S3 integration secret file for {name} is missing.")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"S3 integration secret file for {name} is empty.")
    return value


@dataclass(frozen=True)
class RealS3Context:
    client: S3Client
    store: S3ArtifactStore
    endpoint: str
    bucket: str
    ca_bundle: Path
    workspace_id: UUID

    @property
    def prefix(self) -> str:
        return f"workspaces/{self.workspace_id}/"

    def key(self, kind: ArtifactKind = ArtifactKind.STORED_DOCUMENT) -> str:
        return artifact_key(
            self.workspace_id,
            ArtifactResourceKind.DOCUMENT,
            uuid4(),
            kind,
        )


def create_client() -> tuple[S3Client, str, str, Path]:
    endpoint = required_environment("TEST_S3_ENDPOINT")
    if not endpoint.startswith("https://"):
        raise RuntimeError("S3 integration requires a TLS endpoint.")
    region = required_environment("TEST_S3_REGION")
    bucket = required_environment("TEST_S3_BUCKET")
    ca_bundle = Path(required_environment("TEST_S3_CA_BUNDLE_FILE"))
    if not ca_bundle.is_file():
        raise RuntimeError("S3 integration CA bundle is missing.")
    client: S3Client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=read_secret("TEST_S3_ACCESS_KEY_FILE"),
        aws_secret_access_key=read_secret("TEST_S3_SECRET_KEY_FILE"),
        verify=str(ca_bundle),
        config=Config(
            signature_version="s3v4",
            connect_timeout=2,
            read_timeout=10,
            retries={"total_max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )
    return client, endpoint, bucket, ca_bundle


def create_browser_signing_client(*, ca_bundle: Path) -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url="https://localhost:9000",
        region_name=required_environment("TEST_S3_REGION"),
        aws_access_key_id=read_secret("TEST_S3_ACCESS_KEY_FILE"),
        aws_secret_access_key=read_secret("TEST_S3_SECRET_KEY_FILE"),
        verify=str(ca_bundle),
        config=Config(
            signature_version="s3v4",
            connect_timeout=2,
            read_timeout=10,
            retries={"total_max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )


@pytest.fixture
def s3_context() -> Iterator[RealS3Context]:
    client, endpoint, bucket, ca_bundle = create_client()
    context = RealS3Context(
        client=client,
        store=S3ArtifactStore(client=client, bucket=bucket, max_concurrency=2),
        endpoint=endpoint,
        bucket=bucket,
        ca_bundle=ca_bundle,
        workspace_id=uuid4(),
    )
    yield context

    continuation_token: str | None = None
    while True:
        arguments: dict[str, Any] = {"Bucket": bucket, "Prefix": context.prefix}
        if continuation_token is not None:
            arguments["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**arguments)
        objects = [
            {"Key": item["Key"]}
            for item in response.get("Contents", [])
            if isinstance(item.get("Key"), str)
        ]
        if objects:
            client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")

    uploads = client.list_multipart_uploads(Bucket=bucket, Prefix=context.prefix).get("Uploads", [])
    for upload in uploads:
        key = upload.get("Key")
        upload_id = upload.get("UploadId")
        if isinstance(key, str) and isinstance(upload_id, str):
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)


async def upload(
    context: RealS3Context,
    source: Path,
    *,
    kind: ArtifactKind = ArtifactKind.STORED_DOCUMENT,
) -> tuple[str, bytes]:
    content = await anyio.Path(source).read_bytes()
    key = context.key(kind)
    await context.store.put_file(
        key,
        source,
        media_type="application/pdf",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )
    return key, content


async def test_real_s3_contract_uses_tls_metadata_and_bounded_reads(
    s3_context: RealS3Context,
    tmp_path: Path,
) -> None:
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.7\nreal MinIO adapter\n%%EOF\n")
    key, content = await upload(s3_context, source)

    metadata = await s3_context.store.stat(key)
    assert metadata.storage_key == key
    assert metadata.media_type == "application/pdf"
    assert metadata.size_bytes == len(content)
    assert metadata.sha256 == hashlib.sha256(content).hexdigest()

    chunks: list[bytes] = []
    async with s3_context.store.open_reader(key) as reader:
        while chunk := await reader.read(7):
            assert len(chunk) <= 7
            chunks.append(chunk)
    assert b"".join(chunks) == content

    await s3_context.store.delete(key)
    await s3_context.store.delete(key)
    with pytest.raises(ArtifactNotFoundError):
        await s3_context.store.stat(key)


async def test_real_s3_forces_a_bounded_multipart_upload(
    s3_context: RealS3Context,
    tmp_path: Path,
) -> None:
    source = tmp_path / "multipart.pdf"
    source.write_bytes(b"m" * (MULTIPART_SIZE + 1024))
    key, content = await upload(s3_context, source)

    response = s3_context.client.head_object(Bucket=s3_context.bucket, Key=key)
    assert "-" in str(response["ETag"])
    assert response["Metadata"] == {
        "parserium-sha256": hashlib.sha256(content).hexdigest(),
        "parserium-size": str(len(content)),
    }


async def test_real_s3_rejects_corrupt_provider_metadata(
    s3_context: RealS3Context,
) -> None:
    key = s3_context.key()
    s3_context.client.put_object(
        Bucket=s3_context.bucket,
        Key=key,
        Body=b"corrupt metadata",
        ContentType="application/pdf",
        Metadata={"parserium-sha256": "0" * 64, "parserium-size": "999"},
    )

    with pytest.raises(ArtifactIntegrityError):
        await s3_context.store.stat(key)


async def test_real_s3_signed_get_binds_a_safe_filename_and_expiry(
    s3_context: RealS3Context,
    tmp_path: Path,
) -> None:
    source = tmp_path / "signed.pdf"
    source.write_bytes(b"signed content")
    key, content = await upload(s3_context, source)

    signed_url = await s3_context.store.presign_get(
        key,
        filename='annual "report"\r\n.pdf',
        media_type="application/pdf",
        ttl_seconds=30,
    )
    query = parse_qs(urlsplit(signed_url).query)
    assert query["X-Amz-Expires"] == ["30"]

    tls_context = ssl.create_default_context(cafile=str(s3_context.ca_bundle))
    async with httpx.AsyncClient(verify=tls_context, trust_env=False) as client:
        response = await client.get(signed_url)
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == (
        'attachment; filename="annual _report___.pdf"'
    )


async def test_real_s3_browser_signing_keeps_operations_on_private_endpoint(
    s3_context: RealS3Context,
    tmp_path: Path,
) -> None:
    browser_context = RealS3Context(
        client=s3_context.client,
        store=S3ArtifactStore(
            client=s3_context.client,
            signing_client=create_browser_signing_client(ca_bundle=s3_context.ca_bundle),
            bucket=s3_context.bucket,
            max_concurrency=2,
        ),
        endpoint=s3_context.endpoint,
        bucket=s3_context.bucket,
        ca_bundle=s3_context.ca_bundle,
        workspace_id=s3_context.workspace_id,
    )
    source = tmp_path / "browser-signed.pdf"
    source.write_bytes(b"browser signing uses private object operations")
    key, content = await upload(browser_context, source)

    metadata = await browser_context.store.stat(key)
    assert metadata.size_bytes == len(content)
    chunks: list[bytes] = []
    async with browser_context.store.open_reader(key) as reader:
        while chunk := await reader.read(11):
            chunks.append(chunk)
    assert b"".join(chunks) == content

    url = await browser_context.store.presign_get(
        key,
        filename="browser-signed.pdf",
        media_type="application/pdf",
        ttl_seconds=30,
    )
    parsed = urlsplit(url)
    assert parsed.scheme == "https"
    assert parsed.hostname == "localhost"
    assert parsed.port == 9000
    assert parsed.path.startswith("/parserium-verification-primary/")
    signature_parameter = "X-Amz-" + "Signature"
    assert signature_parameter in parse_qs(parsed.query)


async def test_real_s3_readiness_probe_cleans_up(
    s3_context: RealS3Context,
) -> None:
    key = health_probe_key(f"s3-{uuid4().hex}")

    await s3_context.store.probe(key)

    with pytest.raises(ArtifactNotFoundError):
        await s3_context.store.stat(key)


@dataclass
class CleanupRepository:
    deletion_claims: tuple[ArtifactObjectRecord, ...]
    completed: list[UUID] = field(default_factory=list)

    async def claim_expired_references(self, **kwargs: Any) -> tuple[object, ...]:
        return (object(),)

    async def claim_orphan_objects(self, **kwargs: Any) -> tuple[object, ...]:
        return (object(),)

    async def claim_deletions(self, *args: Any, **kwargs: Any) -> tuple[ArtifactObjectRecord, ...]:
        return self.deletion_claims

    async def complete_deletion(
        self,
        artifact_object_id: UUID,
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        self.completed.append(artifact_object_id)
        return True

    async def fail_deletion(self, *args: Any, **kwargs: Any) -> bool:
        raise AssertionError("Real S3 cleanup must not fail deletion.")

    async def claim_legacy_pending(self, *args: Any, **kwargs: Any) -> tuple[object, ...]:
        return ()

    async def complete_legacy_metadata(self, *args: Any, **kwargs: Any) -> bool:
        raise AssertionError("No legacy object should be claimed.")

    async def fail_legacy_metadata(self, *args: Any, **kwargs: Any) -> bool:
        raise AssertionError("No legacy object should fail.")

    async def list_usage_reconciliation_candidates(self, **kwargs: Any) -> tuple[UUID, ...]:
        return ()

    async def reconcile_usage(self, *args: Any, **kwargs: Any) -> object:
        raise AssertionError("No workspace should require reconciliation.")


class ScratchCleaner:
    async def cleanup_stale(self, *args: Any, **kwargs: Any) -> int:
        return 0


def deleting_record(
    context: RealS3Context,
    key: str,
    content: bytes,
    *,
    now: datetime,
) -> ArtifactObjectRecord:
    return ArtifactObjectRecord(
        id=uuid4(),
        workspace_id=context.workspace_id,
        storage_key=key,
        media_type="application/pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        state=ArtifactObjectState.DELETING,
        available_at=now - timedelta(hours=2),
        delete_attempt_count=0,
        delete_available_at=now,
        delete_claimed_by="s3-maintenance",
        delete_lease_expires_at=now + timedelta(minutes=1),
        failure_code=None,
        created_at=now - timedelta(hours=2),
        updated_at=now,
        deleted_at=None,
    )


async def test_real_s3_maintenance_removes_expired_and_orphaned_objects(
    s3_context: RealS3Context,
    tmp_path: Path,
) -> None:
    expired_source = tmp_path / "expired.pdf"
    expired_source.write_bytes(b"expired temporary artifact")
    orphan_source = tmp_path / "orphan.pdf"
    orphan_source.write_bytes(b"interrupted registration artifact")
    expired_key, expired_content = await upload(s3_context, expired_source)
    orphan_key, orphan_content = await upload(s3_context, orphan_source)
    now = datetime.now(UTC)
    repository = CleanupRepository(
        deletion_claims=(
            deleting_record(s3_context, expired_key, expired_content, now=now),
            deleting_record(s3_context, orphan_key, orphan_content, now=now),
        )
    )
    service = ArtifactMaintenanceService(
        repository=repository,
        artifact_store=s3_context.store,
        scratch_storage=ScratchCleaner(),
        worker_id="s3-maintenance",
        orphan_grace_seconds=60,
        scratch_stale_seconds=300,
        clock=lambda: now,
    )

    assert await service.run_once() is True
    assert len(repository.completed) == 2
    for key in (expired_key, orphan_key):
        with pytest.raises(ArtifactNotFoundError):
            await s3_context.store.stat(key)


async def test_interrupted_multipart_upload_is_aborted(
    s3_context: RealS3Context,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "interrupted.pdf"
    source.write_bytes(b"i" * (MULTIPART_SIZE + 1024))
    key = s3_context.key()
    original_upload_part = s3_context.client.upload_part
    calls = 0

    def interrupted_upload_part(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        original_upload_part(**kwargs)
        raise EndpointConnectionError(endpoint_url=s3_context.endpoint)

    monkeypatch.setattr(s3_context.client, "upload_part", interrupted_upload_part)

    with pytest.raises(ArtifactStorageUnavailableError):
        await s3_context.store.put_file(
            key,
            source,
            media_type="application/pdf",
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            size_bytes=source.stat().st_size,
        )
    assert calls == 1
    uploads = s3_context.client.list_multipart_uploads(
        Bucket=s3_context.bucket,
        Prefix=key,
    ).get("Uploads", [])
    assert uploads == []


def test_verification_bucket_has_no_public_policy(s3_context: RealS3Context) -> None:
    with pytest.raises(ClientError) as captured:
        s3_context.client.get_bucket_policy(Bucket=s3_context.bucket)
    assert captured.value.response["Error"]["Code"] == "NoSuchBucketPolicy"


@pytest.mark.skipif(
    os.environ.get("TEST_S3_EXPECT_UNAVAILABLE") != "1",
    reason="Run only while the verification MinIO service is intentionally stopped.",
)
async def test_real_s3_reports_a_temporary_restart_as_unavailable() -> None:
    client, _, bucket, _ = create_client()
    store = S3ArtifactStore(client=client, bucket=bucket, max_concurrency=1)

    with pytest.raises(ArtifactStorageUnavailableError):
        await store.stat(
            artifact_key(
                uuid4(),
                ArtifactResourceKind.DOCUMENT,
                uuid4(),
                ArtifactKind.STORED_DOCUMENT,
            )
        )


async def test_real_s3_recovers_after_restart(s3_context: RealS3Context) -> None:
    await s3_context.store.probe(health_probe_key(f"restart-{uuid4().hex}"))
