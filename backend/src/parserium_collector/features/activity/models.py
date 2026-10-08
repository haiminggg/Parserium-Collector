from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ActivityJobType(StrEnum):
    DISCOVERY = "discovery"
    COLLECTION = "collection"
    ANALYSIS = "analysis"
    EXPORT = "export"


class ActivityJobState(StrEnum):
    QUEUED = "queued"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ActivityJobRecord:
    id: UUID
    job_type: ActivityJobType
    title: str
    subtitle: str | None
    state: ActivityJobState
    stage: str
    progress_percent: int | None
    created_by_user_id: UUID | None
    created_by_name: str | None
    related_document_id: UUID | None
    error_code: str | None
    retryable: bool | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class ActivitySummary:
    active: int
    queued: int
    failed: int
    completed: int


@dataclass(frozen=True)
class ActivityPage:
    items: tuple[ActivityJobRecord, ...]
    total: int
    next_cursor: str | None
    summary: ActivitySummary


class ActivityJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    job_type: ActivityJobType
    title: str = Field(min_length=1, max_length=500)
    subtitle: str | None = Field(default=None, max_length=500)
    state: ActivityJobState
    stage: str = Field(min_length=1, max_length=32)
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    created_by_user_id: UUID | None
    created_by_name: str | None = Field(default=None, max_length=200)
    related_document_id: UUID | None
    error_code: str | None = Field(default=None, max_length=64)
    can_cancel: bool
    can_retry: bool
    can_delete: bool
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ActivitySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: int = Field(ge=0)
    queued: int = Field(ge=0)
    failed: int = Field(ge=0)
    completed: int = Field(ge=0)


class ActivityPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ActivityJobResponse]
    total: int = Field(ge=0)
    next_cursor: str | None
    summary: ActivitySummaryResponse
