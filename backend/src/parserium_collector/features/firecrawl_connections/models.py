from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from parserium_collector.features.firecrawl_connections.crypto_encoding import (
    decode_canonical_base64,
)


class ConnectionType(StrEnum):
    CLOUD = "cloud"
    REMOTE = "remote"


class ConnectionStatus(StrEnum):
    NEVER_VALIDATED = "never_validated"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    DELETED = "deleted"


class ConnectionFailureCategory(StrEnum):
    INVALID_CREDENTIALS = "invalid_credentials"
    BLOCKED_DESTINATION = "blocked_destination"
    DNS_FAILURE = "dns_failure"
    TLS_FAILURE = "tls_failure"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    RESPONSE_TOO_LARGE = "response_too_large"
    INCOMPATIBLE_RESPONSE = "incompatible_response"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"


class CredentialEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1] = 1
    algorithm: Literal["AES-256-GCM"] = "AES-256-GCM"
    wrapping_algorithm: Literal["AES-256-KW"] = "AES-256-KW"
    key_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    nonce: str
    ciphertext: str
    wrapped_data_key: str

    @field_validator("nonce")
    @classmethod
    def validate_nonce(cls, value: str) -> str:
        if len(decode_canonical_base64(value)) != 12:
            raise ValueError("The credential nonce has an invalid length.")
        return value

    @field_validator("ciphertext")
    @classmethod
    def validate_ciphertext(cls, value: str) -> str:
        if len(decode_canonical_base64(value)) < 16:
            raise ValueError("The credential ciphertext is invalid.")
        return value

    @field_validator("wrapped_data_key")
    @classmethod
    def validate_wrapped_data_key(cls, value: str) -> str:
        if len(decode_canonical_base64(value)) != 40:
            raise ValueError("The wrapped credential key has an invalid length.")
        return value


def normalize_connection_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def validate_credential(value: str) -> str:
    if (
        not value
        or len(value.encode("utf-8")) > 4096
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise ValueError("The Firecrawl credential is invalid.")
    return value


def _normalize_display_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not 1 <= len(normalized) <= 120:
        raise ValueError("The Firecrawl connection name must contain 1 through 120 characters.")
    return normalized


class CreateConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    connection_type: ConnectionType
    base_url: str | None = Field(default=None, max_length=2048)
    credential: str = Field(json_schema_extra={"writeOnly": True})
    is_default: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_display_name(value)

    @field_validator("credential")
    @classmethod
    def validate_token(cls, value: str) -> str:
        return validate_credential(value)

    @model_validator(mode="after")
    def validate_endpoint_shape(self) -> CreateConnectionRequest:
        if self.connection_type is ConnectionType.CLOUD and self.base_url is not None:
            raise ValueError("Cloud Firecrawl connections do not accept a custom endpoint.")
        if self.connection_type is ConnectionType.REMOTE and not self.base_url:
            raise ValueError("Remote Firecrawl connections require an endpoint.")
        return self


class UpdateConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    enabled: bool | None = None
    is_default: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_display_name(value)

    @model_validator(mode="after")
    def require_change(self) -> UpdateConnectionRequest:
        if not self.model_fields_set:
            raise ValueError("At least one connection change is required.")
        return self


class ReplaceCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: str = Field(json_schema_extra={"writeOnly": True})

    @field_validator("credential")
    @classmethod
    def validate_token(cls, value: str) -> str:
        return validate_credential(value)


@dataclass(frozen=True)
class FirecrawlConnectionRecord:
    id: UUID
    workspace_id: UUID
    name: str
    normalized_name: str
    connection_type: ConnectionType
    normalized_base_url: str | None
    credential_envelope: CredentialEnvelope | None
    credential_revision: int
    validated_revision: int | None
    validation_succeeded: bool | None
    capability_profile: dict[str, object] | None
    last_validation_attempt_at: datetime | None
    last_validation_success_at: datetime | None
    last_failure_category: ConnectionFailureCategory | None
    enabled: bool
    is_default: bool
    created_by_user_id: UUID | None
    updated_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @property
    def status(self) -> ConnectionStatus:
        if self.deleted_at is not None:
            return ConnectionStatus.DELETED
        if not self.enabled:
            return ConnectionStatus.DISABLED
        if self.validation_succeeded is None:
            return ConnectionStatus.NEVER_VALIDATED
        if self.validation_succeeded and self.validated_revision == self.credential_revision:
            return ConnectionStatus.HEALTHY
        return ConnectionStatus.DEGRADED

    @property
    def usable(self) -> bool:
        return self.status is ConnectionStatus.HEALTHY


class ConnectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    connection_type: ConnectionType
    status: ConnectionStatus
    enabled: bool
    is_default: bool
    usable: bool
    normalized_base_url: str | None = Field(default=None, exclude_if=lambda value: value is None)
    last_validation_attempt_at: datetime | None
    last_validation_success_at: datetime | None
    last_failure_category: ConnectionFailureCategory | None

    @classmethod
    def from_record(
        cls,
        record: FirecrawlConnectionRecord,
        *,
        include_endpoint: bool,
    ) -> ConnectionSummary:
        return cls(
            id=record.id,
            name=record.name,
            connection_type=record.connection_type,
            status=record.status,
            enabled=record.enabled,
            is_default=record.is_default,
            usable=record.usable,
            normalized_base_url=(record.normalized_base_url if include_endpoint else None),
            last_validation_attempt_at=record.last_validation_attempt_at,
            last_validation_success_at=record.last_validation_success_at,
            last_failure_category=record.last_failure_category,
        )


class ConnectionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connections: tuple[ConnectionSummary, ...]


@dataclass(frozen=True)
class ResolvedConnection:
    id: UUID
    workspace_id: UUID
    name: str
    connection_type: ConnectionType
    normalized_base_url: str | None
    credential: str
    credential_revision: int


@dataclass(frozen=True)
class NewConnection:
    id: UUID
    name: str
    normalized_name: str
    connection_type: ConnectionType
    normalized_base_url: str | None
    credential_envelope: CredentialEnvelope
    credential_revision: int
    enabled: bool
    is_default: bool


@dataclass(frozen=True)
class ConnectionUpdate:
    name: str | None = None
    normalized_name: str | None = None
    normalized_base_url: str | None = None
    endpoint_changed: bool = False
    enabled: bool | None = None
    is_default: bool | None = None


@dataclass(frozen=True)
class ValidationOutcome:
    succeeded: bool
    capability_profile: dict[str, object] | None
    failure_category: ConnectionFailureCategory | None


@dataclass(frozen=True)
class RewrappedEnvelope:
    workspace_id: UUID
    connection_id: UUID
    expected_key_id: str
    envelope: CredentialEnvelope
