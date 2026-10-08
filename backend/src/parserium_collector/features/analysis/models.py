from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from parserium_collector.features.discovery.models import (
    DocumentDiscoveryRequest,
    DocumentType,
)
from parserium_collector.features.firecrawl_connections.models import ConnectionType


class AnalysisSessionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class DiscoveryJobStage(StrEnum):
    QUEUED = "queued"
    DISCOVERING = "discovering"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SubmissionDisposition(StrEnum):
    CREATED = "created"
    ACTIVE_REUSED = "active_reused"
    CACHED_REUSED = "cached_reused"
    FORCE_REFRESH_CREATED = "force_refresh_created"


class DiscoveryCreationReason(StrEnum):
    INITIAL = "initial"
    RETRY = "retry"
    FORCE_REFRESH = "force_refresh"


@dataclass(frozen=True)
class DiscoverySelection:
    """Resolved provider metadata without credential material."""

    connection_id: UUID | None
    connection_name: str | None
    connection_type: ConnectionType | None
    credential_revision: int | None
    provider_identity: str


class CandidateAnalysisStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    VALIDATING = "validating"
    CONVERTING = "converting"
    PARSING = "parsing"
    READY = "ready"
    NO_TABLES = "no_tables"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PROMOTED = "promoted"


class PublicAnalysisState(StrEnum):
    VALID = "valid"
    NO_TABLES = "no_tables"
    PARTIAL = "partial"
    FAILED = "failed"


PUBLIC_ANALYSIS_STATES: Final[Mapping[CandidateAnalysisStatus, PublicAnalysisState]] = (
    MappingProxyType(
        {
            CandidateAnalysisStatus.READY: PublicAnalysisState.VALID,
            CandidateAnalysisStatus.NO_TABLES: PublicAnalysisState.NO_TABLES,
            CandidateAnalysisStatus.PARTIAL: PublicAnalysisState.PARTIAL,
            CandidateAnalysisStatus.FAILED: PublicAnalysisState.FAILED,
        }
    )
)


class AnalysisSearchRequest(DocumentDiscoveryRequest):
    tables_required: bool = True


class DurableAnalysisSearchRequest(AnalysisSearchRequest):
    force_refresh: bool = Field(default=False, strict=True)


class AnalysisCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_ids: tuple[UUID, ...] = Field(min_length=1, max_length=30)

    @field_validator("analysis_ids")
    @classmethod
    def require_unique_analysis_ids(
        cls,
        value: tuple[UUID, ...],
    ) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Candidate analysis identifiers must be unique.")
        return value


class TableBoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class CandidateTableResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    page_num: int = Field(ge=1)
    table_index: int = Field(ge=0)
    bounding_box: TableBoundingBox
    cells: tuple[tuple[str, ...], ...]
    markdown: str


class CandidateTableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_num: int = Field(ge=1)
    table_index: int = Field(ge=0)
    bounding_box: TableBoundingBox
    cells: tuple[tuple[str, ...], ...]
    markdown: str


class AnalysisCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    ordinal: int = Field(ge=0)
    source_url: AnyHttpUrl
    title: str | None
    description: str | None
    document_type: DocumentType
    status: CandidateAnalysisStatus
    public_state: PublicAnalysisState | None
    attempt_count: int = Field(ge=0)
    bytes_downloaded: int = Field(ge=0)
    content_length: int | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=1)
    analyzed_page_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    table_count_lower_bound: bool
    preview_available: bool
    preview_page_num: int | None = Field(default=None, ge=1)
    preview_width: int | None = Field(default=None, ge=1)
    preview_height: int | None = Field(default=None, ge=1)
    error_code: str | None
    error_detail: str | None
    error_retryable: bool | None
    tables: tuple[CandidateTableResponse, ...]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class AnalysisSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    query: str
    document_types: tuple[DocumentType, ...]
    tables_required: bool
    firecrawl_connection_id: UUID | None
    firecrawl_connection_name_snapshot: str | None
    firecrawl_connection_type_snapshot: ConnectionType | None
    status: AnalysisSessionStatus
    job_stage: DiscoveryJobStage
    error_code: str | None
    candidate_count: int = Field(ge=0, le=30)
    bytes_downloaded: int = Field(ge=0)
    session_byte_limit: int = Field(ge=1)
    cancellation_requested: bool
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    completed_at: datetime | None
    candidates: tuple[AnalysisCandidateResponse, ...]


@dataclass(frozen=True)
class AnalysisSessionRecord:
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID | None
    firecrawl_connection_id: UUID | None
    firecrawl_connection_name_snapshot: str | None
    firecrawl_connection_type_snapshot: ConnectionType | None
    query: str
    document_types: tuple[DocumentType, ...]
    include_domains: tuple[str, ...]
    exclude_domains: tuple[str, ...]
    tables_required: bool
    provider_search_ids: tuple[str, ...]
    status: AnalysisSessionStatus
    candidate_count: int
    session_byte_limit: int
    bytes_downloaded: int
    cancellation_requested: bool
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    completed_at: datetime | None
    error_code: str | None
    error_detail: str | None
    request_fingerprint: str | None
    request_fingerprint_version: int | None
    result_limit: int | None
    job_stage: DiscoveryJobStage
    cache_reusable_until: datetime | None
    discovery_claimed_by: str | None
    discovery_lease_expires_at: datetime | None
    provider_request_started_at: datetime | None
    firecrawl_credential_revision_snapshot: int | None
    creation_reason: DiscoveryCreationReason


@dataclass(frozen=True)
class DiscoverySubmission:
    session: AnalysisSessionRecord
    disposition: SubmissionDisposition


@dataclass(frozen=True)
class DiscoveryClaim:
    session: AnalysisSessionRecord
    request: DurableAnalysisSearchRequest
    selection: DiscoverySelection


@dataclass(frozen=True)
class DiscoveryPolicyRecord:
    workspace_id: UUID
    concurrency_limit: int


@dataclass(frozen=True)
class CandidateAnalysisRecord:
    id: UUID
    workspace_id: UUID
    session_id: UUID
    ordinal: int
    source_url: str
    title: str | None
    description: str | None
    document_type: DocumentType
    status: CandidateAnalysisStatus
    attempt_count: int
    available_at: datetime
    claimed_by: str | None
    lease_expires_at: datetime | None
    bytes_downloaded: int
    content_length: int | None
    sha256: str | None
    media_type: str | None
    safe_filename: str | None
    page_count: int | None
    analyzed_page_count: int
    table_count: int
    table_count_lower_bound: bool
    preview_page_num: int | None
    preview_width: int | None
    preview_height: int | None
    error_code: str | None
    error_detail: str | None
    error_retryable: bool | None
    promoted_document_id: UUID | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime


@dataclass(frozen=True)
class CandidateTableRecord:
    id: UUID
    workspace_id: UUID
    candidate_analysis_id: UUID
    page_num: int
    table_index: int
    bounding_box: TableBoundingBox
    cells: tuple[tuple[str, ...], ...]
    markdown: str
    created_at: datetime


@dataclass(frozen=True)
class AnalysisProgress:
    session_id: UUID
    candidate_count: int
    terminal_count: int
    ready_count: int
    no_tables_count: int
    partial_count: int
    failed_count: int
    cancelled_count: int
    bytes_downloaded: int
    session_byte_limit: int
