import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from parserium_collector.features.discovery.models import DocumentType as DocumentType

WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


class CollectionJobStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    VALIDATING = "validating"
    COMPLETED = "completed"
    DUPLICATE = "duplicate"
    FAILED = "failed"


class ExportStatus(StrEnum):
    QUEUED = "queued"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class CollectionJobRecord:
    id: UUID
    workspace_id: UUID
    source_url: str
    title: str | None
    expected_document_type: DocumentType
    status: CollectionJobStatus
    attempt_count: int
    available_at: datetime
    claimed_by: str | None
    lease_expires_at: datetime | None
    bytes_downloaded: int
    content_length: int | None
    document_id: UUID | None
    error_code: str | None
    error_detail: str | None
    error_retryable: bool | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class StoredDocumentRecord:
    id: UUID
    workspace_id: UUID
    sha256: str
    document_type: DocumentType
    media_type: str
    size_bytes: int
    safe_filename: str
    created_at: datetime


@dataclass(frozen=True)
class DocumentExportRecord:
    id: UUID
    workspace_id: UUID
    document_id: UUID
    relative_directory: str
    target_filename: str
    exported_relative_path: str | None
    status: ExportStatus
    attempt_count: int
    available_at: datetime
    claimed_by: str | None
    lease_expires_at: datetime | None
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class CollectionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: AnyHttpUrl
    title: str | None = Field(default=None, max_length=500)
    document_type: DocumentType


class CollectionBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: tuple[CollectionCandidate, ...] = Field(min_length=1, max_length=30)


class DocumentExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_directory: str = Field(default="", max_length=500)

    @field_validator("relative_directory", mode="before")
    @classmethod
    def normalize_relative_directory(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.startswith(("/", "\\")) or WINDOWS_DRIVE_PATTERN.match(stripped):
            raise ValueError("Export directory must be relative to the configured export root.")
        parts = [part for part in stripped.replace("\\", "/").split("/") if part not in ("", ".")]
        if ".." in parts:
            raise ValueError("Export directory cannot contain parent traversal.")
        return "/".join(parts)


class CollectionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_url: AnyHttpUrl
    title: str | None
    expected_document_type: DocumentType
    status: CollectionJobStatus
    attempt_count: int
    available_at: datetime
    bytes_downloaded: int
    content_length: int | None
    document_id: UUID | None
    error_code: str | None
    error_detail: str | None
    error_retryable: bool | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class StoredDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sha256: str
    document_type: DocumentType
    media_type: str
    size_bytes: int
    safe_filename: str
    created_at: datetime


class CollectionJobPageResponse(BaseModel):
    items: list[CollectionJobResponse]
    total: int = Field(ge=0)
    next_cursor: str | None


class StoredDocumentPageResponse(BaseModel):
    items: list[StoredDocumentResponse]
    total: int = Field(ge=0)
    next_cursor: str | None


class DocumentExportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    relative_directory: str
    target_filename: str
    exported_relative_path: str | None
    status: ExportStatus
    attempt_count: int
    available_at: datetime
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
