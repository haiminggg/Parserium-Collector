import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
import pytest
from anyio import Path
from sqlalchemy import URL, RowMapping, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from parserium_collector.adapters.database.tables import (
    candidate_analyses,
    discovery_analysis_sessions,
    discovery_job_events,
    discovery_job_links,
    metadata,
    users,
    workspaces,
)
from parserium_collector.features.analysis.job_repository import PostgresDiscoveryJobRepository
from parserium_collector.features.analysis.models import (
    DiscoveryCreationReason,
    DiscoverySelection,
    DurableAnalysisSearchRequest,
    SubmissionDisposition,
)
from parserium_collector.features.discovery.models import (
    DocumentCandidate,
    DocumentDiscoveryResponse,
)
from parserium_collector.features.discovery.scoped import ScopedDiscoveryResult
from parserium_collector.features.firecrawl_connections.models import ConnectionType
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000002")
USER_A = UUID("20000000-0000-4000-8000-000000000001")
SCOPE_A = WorkspaceScope(WORKSPACE_A, USER_A, WorkspaceRole.OWNER)
SCOPE_B = WorkspaceScope(WORKSPACE_B, USER_A, WorkspaceRole.OWNER)
SELECTION = DiscoverySelection(None, None, None, None, "self-hosted")
FINGERPRINT = "a" * 64


class _InsertRaceRepository(PostgresDiscoveryJobRepository):
    """Test-only barrier that makes every initial lookup finish before any insert."""

    def __init__(self, engine: AsyncEngine, participants: int) -> None:
        super().__init__(engine)
        self._participants = participants
        self._arrivals = 0
        self._arrival_lock = anyio.Lock()
        self._all_arrived = anyio.Event()

    async def _matching_active(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        fingerprint: str,
    ) -> RowMapping | None:
        row = await PostgresDiscoveryJobRepository._matching_active(
            connection,
            workspace_id,
            fingerprint,
        )
        if row is not None:
            return row
        async with self._arrival_lock:
            self._arrivals += 1
            if self._arrivals == self._participants:
                self._all_arrived.set()
        await self._all_arrived.wait()
        return None


class _TerminalWinnerRaceRepository(_InsertRaceRepository):
    """Test-only race that terminalizes the winner before conflict recovery reads it."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine, participants=2)
        self._transitioned = False
        self._transition_lock = anyio.Lock()

    async def _matching_active(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        fingerprint: str,
    ) -> RowMapping | None:
        if self._all_arrived.is_set() and not self._transitioned:
            async with self._transition_lock:
                if not self._transitioned:
                    async with self._engine.begin() as transition_connection:
                        await transition_connection.execute(
                            update(discovery_analysis_sessions)
                            .where(
                                discovery_analysis_sessions.c.workspace_id == workspace_id,
                                discovery_analysis_sessions.c.request_fingerprint == fingerprint,
                                discovery_analysis_sessions.c.status.in_(("queued", "running")),
                            )
                            .values(
                                status="cancelled",
                                job_stage="cancelled",
                                completed_at=NOW + timedelta(seconds=1),
                                updated_at=NOW + timedelta(seconds=1),
                            )
                        )
                    self._transitioned = True
        return await super()._matching_active(connection, workspace_id, fingerprint)


@pytest.fixture
async def database_engine() -> AsyncEngine:
    required = ("TEST_DATABASE_HOST", "TEST_DATABASE_NAME", "TEST_DATABASE_PASSWORD_FILE")
    if any(not os.environ.get(name) for name in required):
        pytest.skip(
            "destructive repository test requires explicit TEST_DATABASE_HOST, "
            "TEST_DATABASE_NAME, and TEST_DATABASE_PASSWORD_FILE"
        )
    password = (
        await Path(os.environ["TEST_DATABASE_PASSWORD_FILE"]).read_text(encoding="utf-8")
    ).strip()
    database_url = URL.create(
        "postgresql+psycopg",
        username=os.environ.get("TEST_DATABASE_USER", "parserium_collector"),
        password=password,
        host=os.environ["TEST_DATABASE_HOST"],
        port=int(os.environ.get("TEST_DATABASE_PORT", "5432")),
        database=os.environ["TEST_DATABASE_NAME"],
    ).render_as_string(hide_password=False)
    engine = create_async_engine(database_url, pool_size=20, max_overflow=0)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(users).values(
                id=USER_A,
                email="owner@example.test",
                normalized_email="owner@example.test",
                display_name="Owner",
                created_at=NOW,
                updated_at=NOW,
                disabled_at=None,
            )
        )
        await connection.execute(
            insert(workspaces),
            [
                {
                    "id": WORKSPACE_A,
                    "name": "Workspace A",
                    "is_local": True,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": WORKSPACE_B,
                    "name": "Workspace B",
                    "is_local": False,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
        await engine.dispose()


def request(*, force_refresh: bool = False, query: str = "quarterly reports"):
    return DurableAnalysisSearchRequest(
        query=query,
        limit=12,
        document_types=("docx", "pdf"),
        include_domains=("z.example", "a.example"),
        force_refresh=force_refresh,
    )


async def submit(
    repository: PostgresDiscoveryJobRepository,
    *,
    scope: WorkspaceScope = SCOPE_A,
    fingerprint: str = FINGERPRINT,
    at: datetime = NOW,
    force_refresh: bool = False,
    reason: DiscoveryCreationReason = DiscoveryCreationReason.INITIAL,
    parent_session_id: UUID | None = None,
):
    return await repository.submit(
        scope,
        request(force_refresh=force_refresh),
        SELECTION,
        fingerprint,
        at,
        at + timedelta(hours=2),
        1024 * 1024,
        reason=reason,
        parent_session_id=parent_session_id,
    )


async def mark_completed(
    engine: AsyncEngine,
    session_id: UUID,
    *,
    at: datetime,
    reusable_until: datetime,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            update(discovery_analysis_sessions)
            .where(discovery_analysis_sessions.c.id == session_id)
            .values(
                status="completed",
                job_stage="completed",
                completed_at=at,
                updated_at=at,
                cache_reusable_until=reusable_until,
            )
        )


async def mark_terminal(
    engine: AsyncEngine,
    session_id: UUID,
    *,
    status: str,
    at: datetime,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            update(discovery_analysis_sessions)
            .where(discovery_analysis_sessions.c.id == session_id)
            .values(
                status=status,
                job_stage=status,
                completed_at=at,
                updated_at=at,
            )
        )


async def test_sequential_active_reuse_is_workspace_scoped_and_canonical(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    created = await submit(repository)
    reused = await submit(repository, at=NOW + timedelta(seconds=1))
    other_workspace = await submit(repository, scope=SCOPE_B, at=NOW + timedelta(seconds=2))

    assert created.disposition is SubmissionDisposition.CREATED
    assert reused.disposition is SubmissionDisposition.ACTIVE_REUSED
    assert reused.session.id == created.session.id
    assert other_workspace.session.id != created.session.id
    assert created.session.document_types == ("docx", "pdf")
    assert created.session.include_domains == ("a.example", "z.example")
    assert created.session.provider_search_ids == ()
    assert created.session.result_limit == 12
    assert created.session.request_fingerprint_version == 1
    assert created.session.candidate_count == 0
    assert created.session.bytes_downloaded == 0


async def test_completed_cache_reuse_is_strict_and_refresh_modes_bypass_it(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    original = await submit(repository)
    deadline = NOW + timedelta(minutes=30)
    await mark_completed(
        database_engine,
        original.session.id,
        at=NOW + timedelta(minutes=1),
        reusable_until=deadline,
    )

    cached = await submit(repository, at=deadline - timedelta(microseconds=1))
    at_equality = await submit(repository, at=deadline)
    await mark_completed(
        database_engine,
        at_equality.session.id,
        at=deadline + timedelta(seconds=1),
        reusable_until=deadline + timedelta(minutes=10),
    )
    forced = await submit(
        repository,
        at=deadline + timedelta(seconds=2),
        force_refresh=True,
        reason=DiscoveryCreationReason.FORCE_REFRESH,
    )

    assert cached.disposition is SubmissionDisposition.CACHED_REUSED
    assert cached.session.id == original.session.id
    assert at_equality.disposition is SubmissionDisposition.CREATED
    assert at_equality.session.id != original.session.id
    assert forced.disposition is SubmissionDisposition.FORCE_REFRESH_CREATED
    assert forced.session.id != at_equality.session.id
    async with database_engine.connect() as connection:
        force_parent = await connection.scalar(
            select(discovery_job_links.c.parent_session_id).where(
                discovery_job_links.c.child_session_id == forced.session.id,
                discovery_job_links.c.workspace_id == WORKSPACE_A,
            )
        )
    assert force_parent == at_equality.session.id


async def test_force_refresh_reuses_an_identical_active_job_before_bypassing_cache(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    active = await submit(repository)

    forced = await submit(
        repository,
        at=NOW + timedelta(seconds=1),
        force_refresh=True,
        reason=DiscoveryCreationReason.FORCE_REFRESH,
    )

    assert forced.disposition is SubmissionDisposition.ACTIVE_REUSED
    assert forced.session.id == active.session.id


async def test_retry_requires_matching_reconstructable_failed_or_cancelled_parent(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    original = await submit(repository)
    await mark_terminal(
        database_engine,
        original.session.id,
        status="failed",
        at=NOW + timedelta(minutes=1),
    )
    retried = await submit(
        repository,
        at=NOW + timedelta(minutes=2),
        reason=DiscoveryCreationReason.RETRY,
        parent_session_id=original.session.id,
    )
    other = await submit(repository, scope=SCOPE_B, fingerprint="b" * 64)

    assert retried.disposition is SubmissionDisposition.CREATED
    assert retried.session.creation_reason is DiscoveryCreationReason.RETRY
    async with database_engine.connect() as connection:
        link = (
            await connection.execute(
                select(discovery_job_links).where(
                    discovery_job_links.c.child_session_id == retried.session.id,
                    discovery_job_links.c.workspace_id == WORKSPACE_A,
                )
            )
        ).mappings().one()
    assert link.parent_session_id == original.session.id
    assert link.reason == "retry"

    other = await submit(repository, scope=SCOPE_B, fingerprint="b" * 64)
    with pytest.raises(LookupError):
        await submit(
            repository,
            fingerprint=FINGERPRINT,
            at=NOW + timedelta(minutes=3),
            reason=DiscoveryCreationReason.RETRY,
            parent_session_id=other.session.id,
        )


@pytest.mark.parametrize(
    ("status", "fingerprint", "result_limit"),
    [
        ("completed", FINGERPRINT, 12),
        ("failed", "b" * 64, 12),
        ("cancelled", FINGERPRINT, None),
    ],
)
async def test_retry_rejects_ineligible_parent_before_identical_active_reuse(
    database_engine: AsyncEngine,
    status: str,
    fingerprint: str,
    result_limit: int | None,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    parent = await submit(repository)
    await mark_terminal(
        database_engine,
        parent.session.id,
        status="failed",
        at=NOW + timedelta(seconds=1),
    )
    await submit(
        repository,
        at=NOW + timedelta(seconds=2),
        reason=DiscoveryCreationReason.RETRY,
        parent_session_id=parent.session.id,
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            update(discovery_analysis_sessions)
            .where(discovery_analysis_sessions.c.id == parent.session.id)
            .values(
                status=status,
                job_stage=status,
                request_fingerprint=fingerprint,
                request_fingerprint_version=1,
                result_limit=result_limit,
            )
        )

    with pytest.raises(LookupError):
        await submit(
            repository,
            at=NOW + timedelta(seconds=3),
            reason=DiscoveryCreationReason.RETRY,
            parent_session_id=parent.session.id,
        )


async def test_explicit_force_parent_must_be_matching_completed_and_unexpired(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    parent = await submit(repository)
    await mark_completed(
        database_engine,
        parent.session.id,
        at=NOW + timedelta(seconds=1),
        reusable_until=NOW + timedelta(minutes=1),
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            update(discovery_analysis_sessions)
            .where(discovery_analysis_sessions.c.id == parent.session.id)
            .values(expires_at=NOW + timedelta(seconds=2), cache_reusable_until=None)
        )

    with pytest.raises(LookupError):
        await submit(
            repository,
            fingerprint=FINGERPRINT,
            at=NOW + timedelta(seconds=2),
            force_refresh=True,
            reason=DiscoveryCreationReason.FORCE_REFRESH,
            parent_session_id=parent.session.id,
        )


async def test_cache_reuse_orders_by_completion_time_not_creation_time(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    created_first = await submit(repository, at=NOW)
    await mark_terminal(
        database_engine,
        created_first.session.id,
        status="failed",
        at=NOW + timedelta(seconds=1),
    )
    created_second = await submit(repository, at=NOW + timedelta(seconds=2))
    await mark_completed(
        database_engine,
        created_second.session.id,
        at=NOW + timedelta(seconds=3),
        reusable_until=NOW + timedelta(hours=1),
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            update(discovery_analysis_sessions)
            .where(discovery_analysis_sessions.c.id == created_first.session.id)
            .values(
                status="completed",
                job_stage="completed",
                completed_at=NOW + timedelta(seconds=4),
                updated_at=NOW + timedelta(seconds=4),
                cache_reusable_until=NOW + timedelta(hours=1),
            )
        )

    reused = await submit(repository, at=NOW + timedelta(seconds=5))

    assert reused.disposition is SubmissionDisposition.CACHED_REUSED
    assert reused.session.id == created_first.session.id


async def test_twenty_concurrent_submissions_have_one_authoritative_session(
    database_engine: AsyncEngine,
) -> None:
    repository = _InsertRaceRepository(database_engine, participants=20)
    results = []

    async def submit_one() -> None:
        results.append(await submit(repository))

    with anyio.fail_after(15):
        async with anyio.create_task_group() as task_group:
            for _ in range(20):
                task_group.start_soon(submit_one)

    assert len({result.session.id for result in results}) == 1
    assert sum(result.disposition is SubmissionDisposition.CREATED for result in results) == 1
    async with database_engine.connect() as connection:
        job_created_count = await connection.scalar(
            select(func.count())
            .select_from(discovery_job_events)
            .where(discovery_job_events.c.event_type == "job_created")
        )
    assert job_created_count == 1


async def test_conflict_restarts_when_winner_becomes_terminal_before_recovery(
    database_engine: AsyncEngine,
) -> None:
    repository = _TerminalWinnerRaceRepository(database_engine)
    results = []

    async def submit_one() -> None:
        results.append(await submit(repository))

    with anyio.fail_after(15):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(submit_one)
            task_group.start_soon(submit_one)

    assert len(results) == 2
    assert len({result.session.id for result in results}) == 2
    assert all(result.disposition is SubmissionDisposition.CREATED for result in results)
    async with database_engine.connect() as connection:
        states = (
            await connection.execute(
                select(discovery_analysis_sessions.c.status).where(
                    discovery_analysis_sessions.c.workspace_id == WORKSPACE_A,
                    discovery_analysis_sessions.c.request_fingerprint == FINGERPRINT,
                )
            )
        ).scalars().all()
    assert sorted(states) == ["cancelled", "queued"]


async def test_non_active_fingerprint_integrity_error_propagates(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    unavailable_selection = DiscoverySelection(
        uuid4(),
        "Unavailable",
        ConnectionType.REMOTE,
        1,
        "test-only-missing-connection",
    )

    with pytest.raises(IntegrityError) as raised:
        await repository.submit(
            SCOPE_A,
            request(),
            unavailable_selection,
            "d" * 64,
            NOW,
            NOW + timedelta(hours=1),
            1024,
            reason=DiscoveryCreationReason.INITIAL,
            parent_session_id=None,
        )

    assert (
        PostgresDiscoveryJobRepository._constraint_name(raised.value)
        != "uq_analysis_sessions_active_fingerprint"
    )


async def test_audit_metadata_is_safe_and_policy_updates_are_scoped(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    await submit(repository)
    await submit(repository, at=NOW + timedelta(seconds=1))
    policy = await repository.update_policy(WORKSPACE_A, 5, USER_A, NOW)

    assert policy.concurrency_limit == 5
    assert (await repository.get_policy(WORKSPACE_A)).concurrency_limit == 5
    assert (await repository.get_policy(WORKSPACE_B)).concurrency_limit == 1
    async with database_engine.connect() as connection:
        events = (
            await connection.execute(
                select(discovery_job_events).where(
                    discovery_job_events.c.workspace_id == WORKSPACE_A
                )
            )
        ).mappings().all()
    for event in events:
        if event.event_type == "concurrency_updated":
            assert event.safe_metadata == {"old": 1, "new": 5}
        else:
            assert event.safe_metadata == {}
        serialized = str(event.safe_metadata)
        assert "quarterly reports" not in serialized
        assert "http" not in serialized
        assert FINGERPRINT not in serialized


async def test_discovery_claim_marker_and_completion_are_atomic(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    submitted = await submit(repository)
    lease_end = NOW + timedelta(minutes=1)

    claim = await repository.claim_discovery("worker-a", NOW, lease_end)

    assert claim is not None
    assert claim.session.id == submitted.session.id
    assert claim.session.job_stage.value == "discovering"
    assert claim.request.query == "quarterly reports"
    assert claim.selection.connection_id is None
    assert claim.selection.credential_revision is None
    assert claim.selection.provider_identity == ""
    assert await repository.mark_provider_request_started(
        claim.session.id,
        "worker-a",
        NOW + timedelta(seconds=1),
    )
    result = ScopedDiscoveryResult(
        response=DocumentDiscoveryResponse(
            provider_search_ids=["search-1"],
            candidates=[
                DocumentCandidate(
                    url="https://reports.example/quarterly.pdf",
                    title="Quarterly report",
                    description=None,
                    document_type="pdf",
                )
            ],
            rejected_non_document_results=0,
        ),
        connection_id=None,
        connection_name_snapshot=None,
        connection_type_snapshot=None,
    )
    assert await repository.complete_discovery(
        claim,
        result,
        NOW + timedelta(seconds=2),
        NOW + timedelta(hours=1),
    )

    async with database_engine.connect() as connection:
        session = (
            await connection.execute(
                select(discovery_analysis_sessions).where(
                    discovery_analysis_sessions.c.id == claim.session.id
                )
            )
        ).mappings().one()
        candidates = (
            await connection.execute(
                select(candidate_analyses).where(
                    candidate_analyses.c.session_id == claim.session.id
                )
            )
        ).mappings().all()
        event_types = (
            await connection.execute(
                select(discovery_job_events.c.event_type)
                .where(discovery_job_events.c.session_id == claim.session.id)
                .order_by(discovery_job_events.c.created_at, discovery_job_events.c.id)
            )
        ).scalars().all()

    assert session.status == "running"
    assert session.job_stage == "analyzing"
    assert session.discovery_claimed_by is None
    assert session.discovery_lease_expires_at is None
    assert session.provider_search_ids == ["search-1"]
    assert len(candidates) == 1
    assert candidates[0].status == "queued"
    assert set(event_types[:2]) == {"job_created", "job_claimed"}
    assert event_types[2:] == ["provider_request_started", "analysis_started"]


async def test_database_rejects_invalid_lifecycle_ownership_and_duplicate_active_fingerprint(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    created = await submit(repository)

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                update(discovery_analysis_sessions)
                .where(discovery_analysis_sessions.c.id == created.session.id)
                .values(status="running")
            )
    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                update(discovery_analysis_sessions)
                .where(discovery_analysis_sessions.c.id == created.session.id)
                .values(discovery_claimed_by="worker-a", discovery_lease_expires_at=None)
            )
    async with database_engine.connect() as connection:
        original = (
            await connection.execute(
                select(discovery_analysis_sessions).where(
                    discovery_analysis_sessions.c.id == created.session.id
                )
            )
        ).mappings().one()
    duplicate = dict(original)
    duplicate["id"] = uuid4()
    duplicate["created_at"] = NOW + timedelta(seconds=1)
    duplicate["updated_at"] = NOW + timedelta(seconds=1)
    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(insert(discovery_analysis_sessions).values(**duplicate))


async def test_database_rejects_incomplete_fingerprint_and_cross_workspace_references(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresDiscoveryJobRepository(database_engine)
    session_a = await submit(repository)
    session_b = await submit(repository, scope=SCOPE_B, fingerprint="b" * 64)

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                update(discovery_analysis_sessions)
                .where(discovery_analysis_sessions.c.id == session_a.session.id)
                .values(request_fingerprint_version=None)
            )

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                insert(discovery_job_links).values(
                    child_session_id=session_a.session.id,
                    workspace_id=WORKSPACE_A,
                    parent_session_id=session_b.session.id,
                    reason="retry",
                    created_at=NOW,
                )
            )

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                insert(discovery_job_events).values(
                    id=uuid4(),
                    workspace_id=WORKSPACE_A,
                    session_id=session_b.session.id,
                    actor_kind="user",
                    actor_user_id=USER_A,
                    event_type="job_created",
                    safe_metadata={},
                    created_at=NOW,
                )
            )
