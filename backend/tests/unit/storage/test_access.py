from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from parserium_collector.features.storage.access import (
    ArtifactAccessService,
    SignedArtifactDownload,
    content_disposition,
)
from parserium_collector.features.storage.errors import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
)
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactObjectState,
    ArtifactStream,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import ArtifactObjectRecord

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")
DOCUMENT_ID = UUID("20000000-0000-4000-8000-000000000001")
CANDIDATE_ID = UUID("30000000-0000-4000-8000-000000000001")
CONTENT = b"test-only artifact content"


def artifact(*, size_bytes: int = len(CONTENT)) -> ArtifactObjectRecord:
    return ArtifactObjectRecord(
        id=uuid4(),
        workspace_id=WORKSPACE_ID,
        storage_key=(f"workspaces/{WORKSPACE_ID}/document/{DOCUMENT_ID}/stored_document"),
        media_type="application/pdf",
        size_bytes=size_bytes,
        sha256="a" * 64,
        state=ArtifactObjectState.AVAILABLE,
        available_at=NOW,
        delete_attempt_count=0,
        delete_available_at=None,
        delete_claimed_by=None,
        delete_lease_expires_at=None,
        failure_code=None,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
    )


class RegistryTestDouble:
    def __init__(self, record: ArtifactObjectRecord | None) -> None:
        self.record = record
        self.events: list[str] = []

    async def resolve_document_object(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> ArtifactObjectRecord | None:
        self.events.append("resolve_document")
        return self.record

    async def resolve_analysis_object(
        self,
        workspace_id: UUID,
        candidate_analysis_id: UUID,
        kind: ArtifactKind,
    ) -> ArtifactObjectRecord | None:
        assert kind is ArtifactKind.PREVIEW_PNG
        self.events.append("resolve_preview")
        return self.record


class ReaderTestDouble:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.content) - self.offset
        chunk = self.content[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class StoreTestDouble:
    def __init__(self, content: bytes = CONTENT, signed_url: str | None = None) -> None:
        self.content = content
        self.signed_url = signed_url
        self.events: list[str] = []
        self.reader_closed = False

    @asynccontextmanager
    async def open_reader(self, storage_key: str) -> AsyncIterator[ReaderTestDouble]:
        self.events.append("open_reader")
        try:
            yield ReaderTestDouble(self.content)
        finally:
            self.reader_closed = True

    async def presign_get(
        self,
        storage_key: str,
        *,
        filename: str,
        media_type: str,
        ttl_seconds: int,
    ) -> str | None:
        self.events.append("presign_get")
        assert filename == "bank-report.pdf"
        assert media_type == "application/pdf"
        assert ttl_seconds == 60
        return self.signed_url

    async def stat(self, storage_key: str) -> StoredObjectMetadata:
        self.events.append("stat")
        return StoredObjectMetadata(
            storage_key=storage_key,
            media_type="application/pdf",
            size_bytes=len(self.content),
            sha256="a" * 64,
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
        raise AssertionError("Access tests must not upload artifacts.")

    async def delete(self, storage_key: str) -> None:
        raise AssertionError("Access tests must not delete artifacts.")

    async def probe(self, probe_key: str) -> None:
        raise AssertionError("Access tests must not probe storage.")


async def collect(stream: ArtifactStream) -> bytes:
    return b"".join([chunk async for chunk in stream.body])


async def test_missing_authorization_precedes_storage_access() -> None:
    registry = RegistryTestDouble(None)
    store = StoreTestDouble(signed_url="https://storage.test/private")
    access = ArtifactAccessService(registry, store, signed_url_ttl_seconds=60)

    with pytest.raises(ArtifactNotFoundError):
        await access.open_document(WORKSPACE_ID, DOCUMENT_ID, "bank-report.pdf")
    with pytest.raises(ArtifactNotFoundError):
        await access.open_preview(WORKSPACE_ID, CANDIDATE_ID)

    assert registry.events == ["resolve_document", "resolve_preview"]
    assert store.events == []


async def test_document_prefers_signed_download_after_authorization() -> None:
    registry = RegistryTestDouble(artifact())
    store = StoreTestDouble(signed_url="https://storage.test/private-signed-target")
    access = ArtifactAccessService(registry, store, signed_url_ttl_seconds=60)

    result = await access.open_document(WORKSPACE_ID, DOCUMENT_ID, "bank-report.pdf")

    assert isinstance(result, SignedArtifactDownload)
    assert result.target == "https://storage.test/private-signed-target"
    assert result.filename == "bank-report.pdf"
    assert registry.events == ["resolve_document"]
    assert store.events == ["presign_get"]


async def test_filesystem_document_and_preview_use_bounded_streams() -> None:
    registry = RegistryTestDouble(artifact())
    store = StoreTestDouble()
    access = ArtifactAccessService(registry, store, signed_url_ttl_seconds=60)

    document = await access.open_document(WORKSPACE_ID, DOCUMENT_ID, "bank-report.pdf")
    preview = await access.open_preview(WORKSPACE_ID, CANDIDATE_ID)

    assert isinstance(document, ArtifactStream)
    assert document.filename == "bank-report.pdf"
    assert await collect(document) == CONTENT
    assert await collect(preview) == CONTENT
    assert store.events == ["presign_get", "open_reader", "open_reader"]
    assert store.reader_closed is True


@pytest.mark.parametrize("content", (CONTENT[:-1], CONTENT + b"unexpected"))
async def test_stream_rejects_registered_size_mismatch_and_closes(content: bytes) -> None:
    registry = RegistryTestDouble(artifact())
    store = StoreTestDouble(content=content)
    access = ArtifactAccessService(registry, store, signed_url_ttl_seconds=60)
    stream = await access.open_preview(WORKSPACE_ID, CANDIDATE_ID)

    with pytest.raises(ArtifactIntegrityError):
        await collect(stream)

    assert store.reader_closed is True


def test_content_disposition_strips_unsafe_characters_and_supports_unicode() -> None:
    header = content_disposition('..\\季度/"report\r\n.pdf')

    assert "\\" not in header
    assert "/" not in header
    assert "\r" not in header
    assert "\n" not in header
    assert 'filename="..___report__.pdf"' in header
    assert "filename*=UTF-8''" in header
