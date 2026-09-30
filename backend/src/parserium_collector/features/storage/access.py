import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote
from uuid import UUID

from parserium_collector.features.storage.errors import (
    ArtifactConfigurationError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
)
from parserium_collector.features.storage.models import ArtifactKind, ArtifactStream
from parserium_collector.features.storage.protocols import ArtifactStore
from parserium_collector.features.storage.repository import ArtifactObjectRecord

STREAM_CHUNK_BYTES = 64 * 1024


class ArtifactAccessRepository(Protocol):
    async def resolve_document_object(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> ArtifactObjectRecord | None: ...

    async def resolve_analysis_object(
        self,
        workspace_id: UUID,
        candidate_analysis_id: UUID,
        kind: ArtifactKind,
    ) -> ArtifactObjectRecord | None: ...


@dataclass(frozen=True)
class SignedArtifactDownload:
    target: str
    media_type: str
    size_bytes: int
    filename: str

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("A signed artifact target must not be empty.")
        if not self.media_type:
            raise ValueError("A signed artifact media type must not be empty.")
        if self.size_bytes < 0:
            raise ValueError("A signed artifact size must not be negative.")
        if not self.filename:
            raise ValueError("A signed artifact filename must not be empty.")


class ArtifactAccessService:
    def __init__(
        self,
        repository: ArtifactAccessRepository,
        store: ArtifactStore,
        *,
        signed_url_ttl_seconds: int,
        chunk_bytes: int = STREAM_CHUNK_BYTES,
    ) -> None:
        if not 30 <= signed_url_ttl_seconds <= 300:
            raise ArtifactConfigurationError("The signed download lifetime is invalid.")
        if chunk_bytes < 1:
            raise ArtifactConfigurationError("The artifact stream chunk size is invalid.")
        self._repository = repository
        self._store = store
        self._signed_url_ttl_seconds = signed_url_ttl_seconds
        self._chunk_bytes = chunk_bytes

    async def open_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
        filename: str,
    ) -> ArtifactStream | SignedArtifactDownload:
        artifact = await self._repository.resolve_document_object(workspace_id, document_id)
        if artifact is None:
            raise ArtifactNotFoundError("The document artifact does not exist.")
        size_bytes = await self._size_after_authorization(artifact)
        safe_filename = sanitize_filename(filename)
        signed_target = await self._store.presign_get(
            artifact.storage_key,
            filename=safe_filename,
            media_type=artifact.media_type,
            ttl_seconds=self._signed_url_ttl_seconds,
        )
        if signed_target is not None:
            return SignedArtifactDownload(
                target=signed_target,
                media_type=artifact.media_type,
                size_bytes=size_bytes,
                filename=safe_filename,
            )
        return self._stream(
            artifact,
            size_bytes=size_bytes,
            media_type=artifact.media_type,
            filename=safe_filename,
        )

    async def open_preview(
        self,
        workspace_id: UUID,
        candidate_analysis_id: UUID,
    ) -> ArtifactStream:
        artifact = await self._repository.resolve_analysis_object(
            workspace_id,
            candidate_analysis_id,
            ArtifactKind.PREVIEW_PNG,
        )
        if artifact is None:
            raise ArtifactNotFoundError("The analysis preview artifact does not exist.")
        size_bytes = await self._size_after_authorization(artifact)
        return self._stream(
            artifact,
            size_bytes=size_bytes,
            media_type="image/png",
            filename=None,
        )

    async def _size_after_authorization(self, artifact: ArtifactObjectRecord) -> int:
        if artifact.size_bytes is not None:
            return artifact.size_bytes
        metadata = await self._store.stat(artifact.storage_key)
        if metadata.storage_key != artifact.storage_key:
            raise ArtifactIntegrityError("Artifact metadata does not match the authorized object.")
        return metadata.size_bytes

    def _stream(
        self,
        artifact: ArtifactObjectRecord,
        *,
        size_bytes: int,
        media_type: str,
        filename: str | None,
    ) -> ArtifactStream:
        return ArtifactStream(
            body=self._bounded_body(artifact.storage_key, size_bytes),
            media_type=media_type,
            size_bytes=size_bytes,
            filename=filename,
        )

    async def _bounded_body(
        self,
        storage_key: str,
        expected_size: int,
    ) -> AsyncIterator[bytes]:
        bytes_read = 0
        async with self._store.open_reader(storage_key) as reader:
            while True:
                chunk = await reader.read(self._chunk_bytes)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > expected_size:
                    raise ArtifactIntegrityError(
                        "The artifact stream exceeded its registered size."
                    )
                yield chunk
        if bytes_read != expected_size:
            raise ArtifactIntegrityError("The artifact stream size did not match its metadata.")


def sanitize_filename(filename: str) -> str:
    normalized = unicodedata.normalize("NFC", filename)
    sanitized = "".join(
        "_"
        if character in {'"', "\\", "/"} or ord(character) < 32 or ord(character) == 127
        else character
        for character in normalized
    ).strip()
    return sanitized or "document"


def content_disposition(filename: str) -> str:
    sanitized = sanitize_filename(filename)
    try:
        sanitized.encode("ascii")
    except UnicodeEncodeError:
        fallback = unicodedata.normalize("NFKD", sanitized)
        fallback = fallback.encode("ascii", "ignore").decode("ascii").strip()
        fallback = sanitize_filename(fallback or "document")
        return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(sanitized, safe='')}"
    return f'attachment; filename="{sanitized}"'
