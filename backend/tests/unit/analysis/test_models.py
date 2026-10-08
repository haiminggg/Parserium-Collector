from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from parserium_collector.features.analysis.models import (
    PUBLIC_ANALYSIS_STATES,
    AnalysisCandidateResponse,
    AnalysisCollectionRequest,
    AnalysisSearchRequest,
    AnalysisSessionRecord,
    AnalysisSessionResponse,
    AnalysisSessionStatus,
    CandidateAnalysisStatus,
    CandidateTableResponse,
    DiscoveryCreationReason,
    DiscoveryJobStage,
    DiscoverySelection,
    DurableAnalysisSearchRequest,
    PublicAnalysisState,
    SubmissionDisposition,
    TableBoundingBox,
)
from parserium_collector.features.discovery.models import DocumentType
from parserium_collector.features.firecrawl_connections.models import ConnectionType

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def test_durable_discovery_enums_have_explicit_values() -> None:
    expected = {
        DiscoveryJobStage: {
            "queued",
            "discovering",
            "analyzing",
            "completed",
            "cancelled",
            "failed",
        },
        SubmissionDisposition: {
            "created",
            "active_reused",
            "cached_reused",
            "force_refresh_created",
        },
        DiscoveryCreationReason: {"initial", "retry", "force_refresh"},
    }
    for enum, values in expected.items():
        assert {member.value for member in enum} == values


def test_discovery_selection_is_frozen_and_contains_no_credentials() -> None:
    selection = DiscoverySelection(
        connection_id=None,
        connection_name=None,
        connection_type=None,
        credential_revision=None,
        provider_identity="deployment-default",
    )
    assert {field.name for field in fields(selection)} == {
        "connection_id",
        "connection_name",
        "connection_type",
        "credential_revision",
        "provider_identity",
    }
    with pytest.raises(FrozenInstanceError):
        selection.provider_identity = "changed"  # type: ignore[misc]


def test_durable_request_accepts_only_strict_force_refresh_boolean() -> None:
    assert DurableAnalysisSearchRequest(query="reports").force_refresh is False
    assert DurableAnalysisSearchRequest(query="reports", force_refresh=True).force_refresh is True
    assert DurableAnalysisSearchRequest(query="reports", force_refresh=False).force_refresh is False
    for value in ("true", "false", 0, 1, None):
        with pytest.raises(ValidationError):
            DurableAnalysisSearchRequest.model_validate(
                {"query": "reports", "force_refresh": value}
            )


def test_analysis_session_record_exposes_durable_submission_state() -> None:
    record = AnalysisSessionRecord(
        id=UUID("10000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("20000000-0000-4000-8000-000000000001"),
        created_by_user_id=None,
        firecrawl_connection_id=None,
        firecrawl_connection_name_snapshot=None,
        firecrawl_connection_type_snapshot=ConnectionType.CLOUD,
        query="reports",
        document_types=(DocumentType.PDF,),
        include_domains=(),
        exclude_domains=(),
        tables_required=True,
        provider_search_ids=(),
        status=AnalysisSessionStatus.QUEUED,
        candidate_count=0,
        session_byte_limit=1024,
        bytes_downloaded=0,
        cancellation_requested=False,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW,
        completed_at=None,
        error_code=None,
        error_detail=None,
        request_fingerprint="a" * 64,
        request_fingerprint_version=1,
        result_limit=20,
        job_stage=DiscoveryJobStage.QUEUED,
        cache_reusable_until=None,
        discovery_claimed_by=None,
        discovery_lease_expires_at=None,
        provider_request_started_at=None,
        firecrawl_credential_revision_snapshot=2,
        creation_reason=DiscoveryCreationReason.INITIAL,
    )

    assert record.request_fingerprint == "a" * 64
    assert record.job_stage is DiscoveryJobStage.QUEUED
    assert record.creation_reason is DiscoveryCreationReason.INITIAL


def test_synchronous_request_does_not_accept_unimplemented_force_refresh() -> None:
    with pytest.raises(ValidationError):
        AnalysisSearchRequest.model_validate({"query": "reports", "force_refresh": True})


def test_analysis_statuses_and_public_terminal_mapping_are_explicit() -> None:
    assert {status.value for status in AnalysisSessionStatus} == {
        "queued",
        "running",
        "completed",
        "cancelled",
        "failed",
    }
    assert {status.value for status in CandidateAnalysisStatus} == {
        "queued",
        "downloading",
        "validating",
        "converting",
        "parsing",
        "ready",
        "no_tables",
        "partial",
        "failed",
        "cancelled",
        "promoted",
    }
    assert PUBLIC_ANALYSIS_STATES == {
        CandidateAnalysisStatus.READY: PublicAnalysisState.VALID,
        CandidateAnalysisStatus.NO_TABLES: PublicAnalysisState.NO_TABLES,
        CandidateAnalysisStatus.PARTIAL: PublicAnalysisState.PARTIAL,
        CandidateAnalysisStatus.FAILED: PublicAnalysisState.FAILED,
    }


def test_analysis_search_request_reuses_strict_discovery_bounds() -> None:
    request = AnalysisSearchRequest(
        query="investment tables",
        limit=30,
        document_types=["pdf", "pdf", "docx"],
        include_domains=["BANK.EXAMPLE", "bank.example"],
        tables_required=True,
    )

    assert request.document_types == ("pdf", "docx")
    assert request.include_domains == ("bank.example",)
    assert request.tables_required is True

    invalid_payloads = (
        {"query": ""},
        {"query": "reports", "limit": 31},
        {"query": "reports", "document_types": []},
        {"query": "reports", "unexpected": True},
    )
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            AnalysisSearchRequest.model_validate(payload)


def test_analysis_collection_requires_one_to_thirty_candidate_ids() -> None:
    analysis_ids = tuple(uuid4() for _ in range(30))
    assert AnalysisCollectionRequest(analysis_ids=analysis_ids).analysis_ids == analysis_ids
    with pytest.raises(ValidationError):
        AnalysisCollectionRequest(analysis_ids=())
    with pytest.raises(ValidationError):
        AnalysisCollectionRequest(analysis_ids=(*analysis_ids, uuid4()))
    with pytest.raises(ValidationError):
        AnalysisCollectionRequest(analysis_ids=(analysis_ids[0], analysis_ids[0]))
    with pytest.raises(ValidationError):
        AnalysisCollectionRequest.model_validate(
            {"analysis_ids": [str(uuid4())], "unexpected": True}
        )


@pytest.mark.parametrize(
    "values",
    (
        {"x": -1, "y": 0, "width": 1, "height": 1},
        {"x": 0, "y": -1, "width": 1, "height": 1},
        {"x": 0, "y": 0, "width": 0, "height": 1},
        {"x": 0, "y": 0, "width": 1, "height": 0},
    ),
)
def test_table_bounding_boxes_reject_invalid_coordinates(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        TableBoundingBox.model_validate(values)


def test_candidate_table_response_has_structured_cells_and_no_internal_paths() -> None:
    response = CandidateTableResponse(
        id=uuid4(),
        page_num=2,
        table_index=0,
        bounding_box={"x": 12.5, "y": 24, "width": 300, "height": 120},
        cells=(("Fund", "NAV"), ("A", "$10")),
        markdown="| Fund | NAV |\n| --- | --- |\n| A | $10 |",
    )

    assert response.bounding_box.x == 12.5
    assert response.cells[1][1] == "$10"
    assert "storage" not in response.model_dump()
    with pytest.raises(ValidationError):
        CandidateTableResponse.model_validate(
            {**response.model_dump(), "storage_key": "temporary/private/table.json"}
        )


def test_public_analysis_snapshot_excludes_worker_and_storage_internals() -> None:
    candidate_id = uuid4()
    candidate = AnalysisCandidateResponse(
        id=candidate_id,
        ordinal=0,
        source_url="https://bank.example/report.pdf",
        title="Annual report",
        description="Investment tables",
        document_type="pdf",
        status="ready",
        public_state="valid",
        attempt_count=1,
        bytes_downloaded=1024,
        content_length=1024,
        page_count=4,
        analyzed_page_count=4,
        table_count=1,
        table_count_lower_bound=False,
        preview_available=True,
        preview_page_num=2,
        preview_width=1275,
        preview_height=1650,
        error_code=None,
        error_detail=None,
        error_retryable=None,
        tables=(),
        created_at=NOW,
        updated_at=NOW,
        completed_at=NOW,
    )
    response = AnalysisSessionResponse(
        id=uuid4(),
        query="bank investment tables",
        document_types=("pdf", "docx"),
        tables_required=True,
        firecrawl_connection_id=None,
        firecrawl_connection_name_snapshot=None,
        firecrawl_connection_type_snapshot=None,
        status="completed",
        job_stage="completed",
        error_code=None,
        candidate_count=1,
        bytes_downloaded=1024,
        session_byte_limit=2048,
        cancellation_requested=False,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        completed_at=NOW,
        candidates=(candidate,),
    )

    payload = response.model_dump(mode="json")
    serialized = str(payload)
    assert payload["candidates"][0]["public_state"] == "valid"
    assert "storage_key" not in serialized
    assert "claimed_by" not in serialized
    assert "lease_expires_at" not in serialized
    with pytest.raises(ValidationError):
        AnalysisCandidateResponse.model_validate(
            {**candidate.model_dump(), "storage_key": "analysis/private/source.pdf"}
        )
