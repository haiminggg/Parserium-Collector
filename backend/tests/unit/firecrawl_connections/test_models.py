from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from parserium_collector.adapters.database.tables import metadata
from parserium_collector.features.firecrawl_connections.errors import (
    ConnectionDefaultConflictError,
    ConnectionNameConflictError,
    ConnectionNotFoundError,
    ConnectionPermissionError,
    ConnectionUnavailableError,
    CredentialUnavailableError,
    RemoteEndpointRejectedError,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionFailureCategory,
    ConnectionStatus,
    ConnectionSummary,
    ConnectionType,
    CreateConnectionRequest,
    FirecrawlConnectionRecord,
    ReplaceCredentialRequest,
    UpdateConnectionRequest,
    normalize_connection_name,
)
from parserium_collector.settings import Settings

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000002")
CONNECTION_ID = UUID("20000000-0000-4000-8000-000000000002")
USER_ID = UUID("30000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def connection_record(
    *,
    enabled: bool = True,
    credential_revision: int = 1,
    validated_revision: int | None = None,
    validation_succeeded: bool | None = None,
    deleted_at: datetime | None = None,
) -> FirecrawlConnectionRecord:
    return FirecrawlConnectionRecord(
        id=CONNECTION_ID,
        workspace_id=WORKSPACE_ID,
        name="Primary Cloud",
        normalized_name="primary cloud",
        connection_type=ConnectionType.CLOUD,
        normalized_base_url=None,
        credential_envelope={"test_double": True},
        credential_revision=credential_revision,
        validated_revision=validated_revision,
        validation_succeeded=validation_succeeded,
        capability_profile=None,
        last_validation_attempt_at=None,
        last_validation_success_at=None,
        last_failure_category=None,
        enabled=enabled,
        is_default=True,
        created_by_user_id=USER_ID,
        updated_by_user_id=USER_ID,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=deleted_at,
    )


def test_firecrawl_connection_metadata_is_workspace_scoped() -> None:
    assert "firecrawl_connections" in metadata.tables
    connections = metadata.tables["firecrawl_connections"]
    sessions = metadata.tables["discovery_analysis_sessions"]

    assert connections.c.workspace_id.nullable is False
    assert connections.c.credential_envelope.nullable is True
    assert {column.name for column in connections.primary_key} == {"id"}
    assert {
        "firecrawl_connection_id",
        "firecrawl_connection_name_snapshot",
        "firecrawl_connection_type_snapshot",
    } <= {column.name for column in sessions.c}


def test_expected_migration_is_current_head() -> None:
    assert Settings().expected_migration == "0009_unified_activity_history"


def test_connection_enums_use_stable_wire_values() -> None:
    assert {item.value for item in ConnectionType} == {"cloud", "remote"}
    assert {item.value for item in ConnectionStatus} == {
        "never_validated",
        "healthy",
        "degraded",
        "disabled",
        "deleted",
    }
    assert {item.value for item in ConnectionFailureCategory} == {
        "invalid_credentials",
        "blocked_destination",
        "dns_failure",
        "tls_failure",
        "timeout",
        "rate_limited",
        "service_unavailable",
        "response_too_large",
        "incompatible_response",
        "credential_unavailable",
    }


@pytest.mark.parametrize(
    ("record", "expected_status", "expected_usable"),
    [
        (connection_record(deleted_at=NOW), ConnectionStatus.DELETED, False),
        (connection_record(enabled=False), ConnectionStatus.DISABLED, False),
        (connection_record(), ConnectionStatus.NEVER_VALIDATED, False),
        (
            connection_record(validated_revision=1, validation_succeeded=True),
            ConnectionStatus.HEALTHY,
            True,
        ),
        (
            connection_record(validated_revision=1, validation_succeeded=False),
            ConnectionStatus.DEGRADED,
            False,
        ),
        (
            connection_record(
                credential_revision=2,
                validated_revision=1,
                validation_succeeded=True,
            ),
            ConnectionStatus.DEGRADED,
            False,
        ),
    ],
)
def test_connection_status_is_derived(
    record: FirecrawlConnectionRecord,
    expected_status: ConnectionStatus,
    expected_usable: bool,
) -> None:
    assert record.status is expected_status
    assert record.usable is expected_usable


def test_cloud_connection_rejects_a_custom_endpoint() -> None:
    with pytest.raises(ValidationError):
        CreateConnectionRequest(
            name="Cloud",
            connection_type="cloud",
            base_url="https://other.example",
            credential="fc-test-only",
        )


def test_remote_connection_requires_an_endpoint() -> None:
    with pytest.raises(ValidationError):
        CreateConnectionRequest(
            name="Remote",
            connection_type="remote",
            credential="remote-test-only",
        )


@pytest.mark.parametrize(
    "credential",
    [
        "",
        "a" * 4097,
        "é" * 2049,
        "line\rbreak",
        "line\nbreak",
        "nul\0byte",
        "unit\x1fseparator",
        "delete\x7fcharacter",
        "next\u0085line",
    ],
)
def test_connection_requests_reject_unsafe_credentials(credential: str) -> None:
    with pytest.raises(ValidationError):
        CreateConnectionRequest(
            name="Cloud",
            connection_type="cloud",
            credential=credential,
        )
    with pytest.raises(ValidationError):
        ReplaceCredentialRequest(credential=credential)


def test_connection_requests_preserve_valid_credentials_exactly() -> None:
    request = CreateConnectionRequest(
        name="Cloud",
        connection_type="cloud",
        credential="test token with spaces",
    )
    replacement = ReplaceCredentialRequest(credential="replacement token")

    assert request.credential == "test token with spaces"
    assert replacement.credential == "replacement token"


def test_names_are_trimmed_and_case_normalized() -> None:
    request = CreateConnectionRequest(
        name="  Primary Cloud  ",
        connection_type="cloud",
        credential="fc-test-only",
    )

    assert request.name == "Primary Cloud"
    assert normalize_connection_name(request.name) == "primary cloud"


def test_update_requires_at_least_one_change() -> None:
    with pytest.raises(ValidationError):
        UpdateConnectionRequest()


def test_safe_summary_has_no_credential_fields() -> None:
    summary = ConnectionSummary.from_record(connection_record(), include_endpoint=False)

    assert "credential" not in ConnectionSummary.model_fields
    assert "credential_envelope" not in ConnectionSummary.model_fields
    assert "credential_revision" not in ConnectionSummary.model_fields
    assert summary.normalized_base_url is None


@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        (ConnectionNotFoundError, "The Firecrawl connection is unavailable."),
        (ConnectionPermissionError, "Workspace owner access is required."),
        (ConnectionNameConflictError, "A Firecrawl connection already uses that name."),
        (
            ConnectionDefaultConflictError,
            "The default Firecrawl connection could not be changed.",
        ),
        (ConnectionUnavailableError, "The Firecrawl connection is not usable."),
        (CredentialUnavailableError, "The Firecrawl credential is unavailable."),
        (RemoteEndpointRejectedError, "The remote Firecrawl endpoint is not allowed."),
    ],
)
def test_connection_errors_use_fixed_safe_messages(
    error_type: type[RuntimeError],
    message: str,
) -> None:
    assert str(error_type()) == message
