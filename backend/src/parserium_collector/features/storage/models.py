import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ArtifactObjectState(StrEnum):
    LEGACY_PENDING = "legacy_pending"
    UPLOADING = "uploading"
    AVAILABLE = "available"
    DELETING = "deleting"
    DELETE_FAILED = "delete_failed"
    DELETED = "deleted"


class ArtifactLifecycle(StrEnum):
    TEMPORARY = "temporary"
    PERSISTENT = "persistent"


class ArtifactResourceKind(StrEnum):
    ANALYSIS = "analysis"
    DOCUMENT = "document"
    INTERNAL = "internal"


class ArtifactKind(StrEnum):
    SOURCE_PDF = "source_pdf"
    SOURCE_DOCX = "source_docx"
    CONVERTED_PDF = "converted_pdf"
    PREVIEW_PNG = "preview_png"
    STORED_DOCUMENT = "stored_document"


@dataclass(frozen=True)
class StoredObjectMetadata:
    storage_key: str
    media_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.media_type.strip():
            raise ValueError("Artifact media type must not be empty.")
        if self.size_bytes < 0:
            raise ValueError("Artifact size must not be negative.")
        if SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise ValueError("Artifact SHA-256 must be lowercase hexadecimal.")


@dataclass(frozen=True)
class ArtifactStream:
    body: AsyncIterator[bytes]
    media_type: str
    size_bytes: int
    filename: str | None

    def __post_init__(self) -> None:
        if not self.media_type.strip():
            raise ValueError("Artifact media type must not be empty.")
        if self.size_bytes < 0:
            raise ValueError("Artifact size must not be negative.")
