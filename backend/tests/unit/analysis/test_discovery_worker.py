from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.analysis.discovery_worker import DiscoveryJobWorker
from parserium_collector.features.analysis.fingerprint import fingerprint_request
from parserium_collector.features.analysis.models import (
    AnalysisSessionRecord,
    AnalysisSessionStatus,
    DiscoveryClaim,
    DiscoveryCreationReason,
    DiscoveryJobStage,
    DiscoverySelection,
    DurableAnalysisSearchRequest,
)
from parserium_collector.features.discovery.models import (
    DocumentCandidate,
    DocumentDiscoveryResponse,
    DocumentType,
)
from parserium_collector.features.discovery.scoped import ScopedDiscoveryResult

NOW = datetime(2026, 9, 7, 15, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000041")
SESSION_ID = UUID("20000000-0000-4000-8000-000000000041")
SECRET = b"f" * 32
REQUEST = DurableAnalysisSearchRequest(query="annual reports", document_types=("pdf",))
SELECTION = DiscoverySelection(None, None, None, None, "self-hosted-test")


def session() -> AnalysisSessionRecord:
    return AnalysisSessionRecord(
        id=SESSION_ID,
        workspace_id=WORKSPACE_ID,
        created_by_user_id=None,
        firecrawl_connection_id=None,
        firecrawl_connection_name_snapshot=None,
        firecrawl_connection_type_snapshot=None,
        query=REQUEST.query,
        document_types=(DocumentType.PDF,),
        include_domains=(),
        exclude_domains=(),
        tables_required=True,
        provider_search_ids=(),
        status=AnalysisSessionStatus.RUNNING,
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
        request_fingerprint=fingerprint_request(SECRET, WORKSPACE_ID, REQUEST, SELECTION),
        request_fingerprint_version=1,
        result_limit=REQUEST.limit,
        job_stage=DiscoveryJobStage.DISCOVERING,
        cache_reusable_until=None,
        discovery_claimed_by="discovery-worker-test",
        discovery_lease_expires_at=NOW + timedelta(seconds=60),
        provider_request_started_at=None,
        firecrawl_credential_revision_snapshot=None,
        creation_reason=DiscoveryCreationReason.INITIAL,
    )


class DiscoveryRepositoryTestDouble:
    def __init__(self, claim: DiscoveryClaim | None) -> None:
        self.claim = claim
        self.provider_started: UUID | None = None
        self.completed: tuple[DiscoveryClaim, ScopedDiscoveryResult] | None = None
        self.failure_code: str | None = None
        self.cancelled = False
        self.cancellation_requested = False

    async def recover_uncertain_discovery(self, now: datetime) -> bool:
        return False

    async def claim_discovery(self, worker_id: str, now: datetime, lease_expires_at: datetime):
        claim, self.claim = self.claim, None
        return claim

    async def renew_discovery_lease(self, *args, **kwargs) -> bool:
        return True

    async def mark_provider_request_started(
        self, session_id: UUID, worker_id: str, now: datetime
    ) -> bool:
        self.provider_started = session_id
        return True

    async def is_discovery_cancellation_requested(self, session_id: UUID, worker_id: str) -> bool:
        return self.cancellation_requested

    async def complete_discovery(
        self,
        claim: DiscoveryClaim,
        result: ScopedDiscoveryResult,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        self.completed = (claim, result)
        return True

    async def fail_discovery(
        self, session_id: UUID, worker_id: str, code: str, now: datetime
    ) -> bool:
        self.failure_code = code
        return True

    async def cancel_discovery(self, session_id: UUID, worker_id: str, now: datetime) -> bool:
        self.cancelled = True
        return True


class DiscoveryProviderTestDouble:
    """Minimal provider test double. It never opens a network connection."""

    def __init__(self) -> None:
        self.calls: list[UUID] = []
        self.selection = SELECTION
        self.failure: Exception | None = None

    async def prepare_for_job(
        self, workspace_id: UUID, persisted: DiscoverySelection
    ) -> DiscoverySelection:
        return self.selection

    async def search_prepared(self, workspace_id, request, selection, now):
        self.calls.append(SESSION_ID)
        if self.failure is not None:
            raise self.failure
        return ScopedDiscoveryResult(
            DocumentDiscoveryResponse(
                provider_search_ids=["search-1"],
                candidates=[
                    DocumentCandidate(
                        url="https://bank.example/annual.pdf",
                        title="Annual report",
                        document_type="pdf",
                    )
                ],
                rejected_non_document_results=0,
            ),
            None,
            None,
            None,
        )


def worker(repository, provider) -> DiscoveryJobWorker:
    return DiscoveryJobWorker(
        repository=repository,
        discovery=provider,
        fingerprint_secret=SECRET,
        worker_id="discovery-worker-test",
        lease_seconds=60,
        clock=lambda: NOW,
    )


async def test_worker_marks_dispatch_once_and_hands_candidates_to_analysis() -> None:
    claim = DiscoveryClaim(session(), REQUEST, SELECTION)
    repository = DiscoveryRepositoryTestDouble(claim)
    provider = DiscoveryProviderTestDouble()

    assert await worker(repository, provider).run_once() is True

    assert provider.calls == [SESSION_ID]
    assert repository.provider_started == SESSION_ID
    assert repository.completed is not None
    assert repository.completed[1].response.candidates[0].title == "Annual report"
    assert repository.failure_code is None


async def test_post_dispatch_claim_never_calls_provider_again() -> None:
    claimed = replace(session(), provider_request_started_at=NOW - timedelta(seconds=1))
    repository = DiscoveryRepositoryTestDouble(DiscoveryClaim(claimed, REQUEST, SELECTION))
    provider = DiscoveryProviderTestDouble()

    assert await worker(repository, provider).run_once() is True

    assert provider.calls == []
    assert repository.failure_code == "provider_outcome_unknown"


async def test_changed_provider_identity_fails_before_dispatch() -> None:
    repository = DiscoveryRepositoryTestDouble(DiscoveryClaim(session(), REQUEST, SELECTION))
    provider = DiscoveryProviderTestDouble()
    provider.selection = replace(SELECTION, provider_identity="changed")

    assert await worker(repository, provider).run_once() is True

    assert repository.provider_started is None
    assert provider.calls == []
    assert repository.failure_code == "connection_unavailable"


async def test_cancellation_before_dispatch_never_calls_provider() -> None:
    repository = DiscoveryRepositoryTestDouble(DiscoveryClaim(session(), REQUEST, SELECTION))
    repository.cancellation_requested = True
    provider = DiscoveryProviderTestDouble()

    assert await worker(repository, provider).run_once() is True

    assert repository.cancelled is True
    assert repository.provider_started is None
    assert provider.calls == []


@pytest.mark.parametrize(
    ("provider_code", "expected_code"),
    [
        ("invalid_credentials", "provider_authentication_failed"),
        ("rate_limited", "provider_rate_limited"),
        ("timeout", "provider_timeout"),
        ("incompatible_response", "provider_invalid_response"),
        ("dns_failure", "connection_unavailable"),
    ],
)
async def test_provider_failures_are_mapped_to_safe_codes(
    provider_code: str,
    expected_code: str,
) -> None:
    repository = DiscoveryRepositoryTestDouble(DiscoveryClaim(session(), REQUEST, SELECTION))
    provider = DiscoveryProviderTestDouble()
    provider.failure = FirecrawlAdapterError(code=provider_code, retryable=False)

    assert await worker(repository, provider).run_once() is True

    assert repository.failure_code == expected_code
