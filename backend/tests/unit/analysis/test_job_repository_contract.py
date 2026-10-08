from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from inspect import signature
from uuid import UUID

import pytest

from parserium_collector.features.analysis.job_repository import (
    DiscoveryJobRepository,
    PostgresDiscoveryJobRepository,
)
from parserium_collector.features.analysis.models import (
    AnalysisSessionRecord,
    AnalysisSessionStatus,
    DiscoveryClaim,
    DiscoveryCreationReason,
    DiscoveryJobStage,
    DiscoveryPolicyRecord,
    DiscoverySelection,
    DiscoverySubmission,
    DurableAnalysisSearchRequest,
    SubmissionDisposition,
)
from parserium_collector.features.discovery.models import DocumentType
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")
USER_ID = UUID("20000000-0000-4000-8000-000000000001")
SCOPE = WorkspaceScope(WORKSPACE_ID, USER_ID, WorkspaceRole.OWNER)
SELECTION = DiscoverySelection(None, None, None, None, "self-hosted")


def _session() -> AnalysisSessionRecord:
    return AnalysisSessionRecord(
        id=UUID("30000000-0000-4000-8000-000000000001"),
        workspace_id=WORKSPACE_ID,
        created_by_user_id=USER_ID,
        firecrawl_connection_id=None,
        firecrawl_connection_name_snapshot=None,
        firecrawl_connection_type_snapshot=None,
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
        expires_at=NOW + timedelta(hours=1),
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
        firecrawl_credential_revision_snapshot=None,
        creation_reason=DiscoveryCreationReason.INITIAL,
    )


def test_postgres_repository_implements_submission_and_policy_contract() -> None:
    for method_name in (
        "submit",
        "get_policy",
        "update_policy",
        "claim_discovery",
        "renew_discovery_lease",
        "mark_provider_request_started",
        "is_discovery_cancellation_requested",
        "complete_discovery",
        "fail_discovery",
        "cancel_discovery",
        "recover_uncertain_discovery",
    ):
        assert signature(getattr(PostgresDiscoveryJobRepository, method_name)) == signature(
            getattr(DiscoveryJobRepository, method_name)
        )


def test_job_handoff_records_are_immutable() -> None:
    session = _session()
    submission = DiscoverySubmission(session, SubmissionDisposition.CREATED)
    claim = DiscoveryClaim(
        session,
        DurableAnalysisSearchRequest(query="reports"),
        SELECTION,
    )
    policy = DiscoveryPolicyRecord(WORKSPACE_ID, 2)

    with pytest.raises(FrozenInstanceError):
        submission.disposition = SubmissionDisposition.CACHED_REUSED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        claim.selection = SELECTION  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        policy.concurrency_limit = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    ("fingerprint", "byte_limit", "expires_at", "reason", "parent_session_id"),
    [
        ("A" * 64, 1024, NOW + timedelta(hours=1), DiscoveryCreationReason.INITIAL, None),
        ("a" * 63, 1024, NOW + timedelta(hours=1), DiscoveryCreationReason.INITIAL, None),
        ("a" * 64, 0, NOW + timedelta(hours=1), DiscoveryCreationReason.INITIAL, None),
        ("a" * 64, 1024, NOW, DiscoveryCreationReason.INITIAL, None),
        ("a" * 64, 1024, NOW + timedelta(hours=1), DiscoveryCreationReason.RETRY, None),
    ],
)
async def test_submit_rejects_invalid_internal_arguments_before_database_access(
    fingerprint: str,
    byte_limit: int,
    expires_at: datetime,
    reason: DiscoveryCreationReason,
    parent_session_id: UUID | None,
) -> None:
    repository = PostgresDiscoveryJobRepository(object())  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        await repository.submit(
            SCOPE,
            DurableAnalysisSearchRequest(query="reports"),
            SELECTION,
            fingerprint,
            NOW,
            expires_at,
            byte_limit,
            reason=reason,
            parent_session_id=parent_session_id,
        )


@pytest.mark.parametrize("concurrency_limit", [0, 6])
async def test_policy_update_rejects_out_of_range_values_before_database_access(
    concurrency_limit: int,
) -> None:
    repository = PostgresDiscoveryJobRepository(object())  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        await repository.update_policy(WORKSPACE_ID, concurrency_limit, USER_ID, NOW)


class _NoDatabaseConnection:
    async def execute(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid metadata reached the database")


@pytest.mark.parametrize(
    ("event_type", "safe_metadata"),
    [
        ("job_created", {"query": "sensitive"}),
        ("active_reused", {"old": 1}),
        ("concurrency_updated", {"old": 1}),
        ("concurrency_updated", {"old": 1, "new": 2, "extra": 3}),
        ("concurrency_updated", {"old": True, "new": 2}),
        ("concurrency_updated", {"old": 1, "new": "2"}),
        ("unknown_event", {}),
    ],
)
async def test_event_metadata_allow_list_rejects_unknown_keys_and_non_integer_values(
    event_type: str,
    safe_metadata: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        await PostgresDiscoveryJobRepository._insert_event(
            _NoDatabaseConnection(),  # type: ignore[arg-type]
            WORKSPACE_ID,
            None,
            USER_ID,
            event_type,
            NOW,
            safe_metadata,
        )
