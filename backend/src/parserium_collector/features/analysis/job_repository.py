import re
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, and_, exists, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from parserium_collector.adapters.database.tables import (
    candidate_analyses,
    discovery_analysis_sessions,
    discovery_job_events,
    discovery_job_links,
    workspaces,
)
from parserium_collector.features.analysis.models import (
    AnalysisSessionStatus,
    CandidateAnalysisStatus,
    DiscoveryClaim,
    DiscoveryCreationReason,
    DiscoveryPolicyRecord,
    DiscoverySelection,
    DiscoverySubmission,
    DurableAnalysisSearchRequest,
    SubmissionDisposition,
)
from parserium_collector.features.analysis.repository import session_record_from_row
from parserium_collector.features.discovery.scoped import ScopedDiscoveryResult
from parserium_collector.features.firecrawl_connections.models import ConnectionType
from parserium_collector.features.identity.models import WorkspaceScope

_ACTIVE_FINGERPRINT_CONSTRAINT = "uq_analysis_sessions_active_fingerprint"
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CACHE_REUSE_DURATION = timedelta(minutes=15)
_PUBLIC_FAILURE_CODES = frozenset(
    {
        "connection_unavailable",
        "provider_authentication_failed",
        "provider_rate_limited",
        "provider_timeout",
        "provider_invalid_response",
        "provider_outcome_unknown",
        "local_analysis_failed",
        "internal_processing_failed",
    }
)
_EVENT_METADATA_KEYS = {
    "job_created": frozenset(),
    "active_reused": frozenset(),
    "cached_reused": frozenset(),
    "force_refresh_created": frozenset(),
    "job_claimed": frozenset(),
    "provider_request_started": frozenset(),
    "analysis_started": frozenset(),
    "job_completed": frozenset(),
    "job_failed": frozenset(),
    "cancellation_requested": frozenset(),
    "job_cancelled": frozenset(),
    "retry_created": frozenset(),
    "concurrency_updated": frozenset({"old", "new"}),
}


class DiscoveryJobRepository(Protocol):
    async def submit(
        self,
        scope: WorkspaceScope,
        request: DurableAnalysisSearchRequest,
        selection: DiscoverySelection,
        fingerprint: str,
        now: datetime,
        expires_at: datetime,
        byte_limit: int,
        *,
        reason: DiscoveryCreationReason,
        parent_session_id: UUID | None,
    ) -> DiscoverySubmission: ...

    async def get_policy(self, workspace_id: UUID) -> DiscoveryPolicyRecord: ...

    async def update_policy(
        self,
        workspace_id: UUID,
        concurrency_limit: int,
        actor_user_id: UUID,
        now: datetime,
    ) -> DiscoveryPolicyRecord: ...

    async def claim_discovery(
        self, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> DiscoveryClaim | None: ...

    async def renew_discovery_lease(
        self,
        session_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool: ...

    async def mark_provider_request_started(
        self, session_id: UUID, worker_id: str, now: datetime
    ) -> bool: ...

    async def is_discovery_cancellation_requested(
        self, session_id: UUID, worker_id: str
    ) -> bool: ...

    async def complete_discovery(
        self,
        claim: DiscoveryClaim,
        result: ScopedDiscoveryResult,
        now: datetime,
        expires_at: datetime,
    ) -> bool: ...

    async def fail_discovery(
        self, session_id: UUID, worker_id: str, code: str, now: datetime
    ) -> bool: ...

    async def cancel_discovery(
        self, session_id: UUID, worker_id: str, now: datetime
    ) -> bool: ...

    async def recover_uncertain_discovery(self, now: datetime) -> bool: ...


class PostgresDiscoveryJobRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def submit(
        self,
        scope: WorkspaceScope,
        request: DurableAnalysisSearchRequest,
        selection: DiscoverySelection,
        fingerprint: str,
        now: datetime,
        expires_at: datetime,
        byte_limit: int,
        *,
        reason: DiscoveryCreationReason,
        parent_session_id: UUID | None,
    ) -> DiscoverySubmission:
        self._validate_submission(
            request,
            fingerprint,
            now,
            expires_at,
            byte_limit,
            reason,
            parent_session_id,
        )
        async with self._engine.begin() as connection:
            if parent_session_id is not None:
                await self._require_eligible_parent(
                    connection,
                    scope.workspace_id,
                    parent_session_id,
                    fingerprint,
                    reason,
                    now,
                )

            active = await self._matching_active(
                connection,
                scope.workspace_id,
                fingerprint,
            )
            if active is not None:
                await self._insert_event(
                    connection,
                    scope.workspace_id,
                    active.id,
                    scope.user_id,
                    "active_reused",
                    now,
                )
                return DiscoverySubmission(
                    session_record_from_row(active),
                    SubmissionDisposition.ACTIVE_REUSED,
                )

            if not request.force_refresh and reason is not DiscoveryCreationReason.RETRY:
                cached = await self._matching_completed(
                    connection,
                    scope.workspace_id,
                    fingerprint,
                    now,
                    reusable_only=True,
                )
                if cached is not None:
                    await self._insert_event(
                        connection,
                        scope.workspace_id,
                        cached.id,
                        scope.user_id,
                        "cached_reused",
                        now,
                    )
                    return DiscoverySubmission(
                        session_record_from_row(cached),
                        SubmissionDisposition.CACHED_REUSED,
                    )

            link_parent_id = parent_session_id
            if link_parent_id is None and reason is DiscoveryCreationReason.FORCE_REFRESH:
                parent = await self._matching_completed(
                    connection,
                    scope.workspace_id,
                    fingerprint,
                    now,
                    reusable_only=False,
                )
                if parent is not None:
                    link_parent_id = parent.id

            session_id = uuid4()
            values = {
                "id": session_id,
                "workspace_id": scope.workspace_id,
                "owner_session_digest": None,
                "created_by_user_id": scope.user_id,
                "firecrawl_connection_id": selection.connection_id,
                "firecrawl_connection_name_snapshot": selection.connection_name,
                "firecrawl_connection_type_snapshot": (
                    selection.connection_type.value
                    if selection.connection_type is not None
                    else None
                ),
                "query": request.query,
                "document_types": sorted({value.value for value in request.document_types}),
                "include_domains": sorted(set(request.include_domains)),
                "exclude_domains": sorted(set(request.exclude_domains)),
                "tables_required": request.tables_required,
                "provider_search_ids": [],
                "status": AnalysisSessionStatus.QUEUED.value,
                "candidate_count": 0,
                "session_byte_limit": byte_limit,
                "bytes_downloaded": 0,
                "cancellation_requested": False,
                "error_code": None,
                "error_detail": None,
                "created_at": now,
                "updated_at": now,
                "expires_at": expires_at,
                "completed_at": None,
                "request_fingerprint": fingerprint,
                "request_fingerprint_version": 1,
                "result_limit": request.limit,
                "job_stage": "queued",
                "cache_reusable_until": None,
                "discovery_claimed_by": None,
                "discovery_lease_expires_at": None,
                "provider_request_started_at": None,
                "firecrawl_credential_revision_snapshot": selection.credential_revision,
                "creation_reason": reason.value,
            }
            while True:
                savepoint = await connection.begin_nested()
                try:
                    created = (
                        (
                            await connection.execute(
                                insert(discovery_analysis_sessions)
                                .values(**values)
                                .returning(*discovery_analysis_sessions.c)
                            )
                        )
                        .mappings()
                        .one()
                    )
                except IntegrityError as error:
                    await savepoint.rollback()
                    if self._constraint_name(error) != _ACTIVE_FINGERPRINT_CONSTRAINT:
                        raise
                    winner = await self._matching_active(
                        connection,
                        scope.workspace_id,
                        fingerprint,
                    )
                    if winner is not None:
                        await self._insert_event(
                            connection,
                            scope.workspace_id,
                            winner.id,
                            scope.user_id,
                            "active_reused",
                            now,
                        )
                        return DiscoverySubmission(
                            session_record_from_row(winner),
                            SubmissionDisposition.ACTIVE_REUSED,
                        )
                    if not request.force_refresh and reason is not DiscoveryCreationReason.RETRY:
                        cached = await self._matching_completed(
                            connection,
                            scope.workspace_id,
                            fingerprint,
                            now,
                            reusable_only=True,
                        )
                        if cached is not None:
                            await self._insert_event(
                                connection,
                                scope.workspace_id,
                                cached.id,
                                scope.user_id,
                                "cached_reused",
                                now,
                            )
                            return DiscoverySubmission(
                                session_record_from_row(cached),
                                SubmissionDisposition.CACHED_REUSED,
                            )
                    continue
                else:
                    await savepoint.commit()
                    break

            await self._insert_event(
                connection,
                scope.workspace_id,
                session_id,
                scope.user_id,
                "job_created",
                now,
            )
            if reason in {
                DiscoveryCreationReason.FORCE_REFRESH,
                DiscoveryCreationReason.RETRY,
            }:
                await self._insert_event(
                    connection,
                    scope.workspace_id,
                    session_id,
                    scope.user_id,
                    (
                        "force_refresh_created"
                        if reason is DiscoveryCreationReason.FORCE_REFRESH
                        else "retry_created"
                    ),
                    now,
                )
            if link_parent_id is not None:
                await connection.execute(
                    insert(discovery_job_links).values(
                        child_session_id=session_id,
                        workspace_id=scope.workspace_id,
                        parent_session_id=link_parent_id,
                        reason=reason.value,
                        created_at=now,
                    )
                )
            disposition = (
                SubmissionDisposition.FORCE_REFRESH_CREATED
                if reason is DiscoveryCreationReason.FORCE_REFRESH
                else SubmissionDisposition.CREATED
            )
            return DiscoverySubmission(session_record_from_row(created), disposition)

    async def get_policy(self, workspace_id: UUID) -> DiscoveryPolicyRecord:
        async with self._engine.connect() as connection:
            concurrency_limit = await connection.scalar(
                select(workspaces.c.discovery_concurrency_limit).where(
                    workspaces.c.id == workspace_id
                )
            )
        if concurrency_limit is None:
            raise LookupError("The discovery workspace policy is unavailable.")
        return DiscoveryPolicyRecord(workspace_id, concurrency_limit)

    async def claim_discovery(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DiscoveryClaim | None:
        if not worker_id or lease_expires_at <= now:
            raise ValueError("Discovery claim ownership and lease must be valid.")
        eligible_job = or_(
            and_(
                discovery_analysis_sessions.c.status == "queued",
                discovery_analysis_sessions.c.job_stage == "queued",
            ),
            and_(
                discovery_analysis_sessions.c.status == "running",
                discovery_analysis_sessions.c.job_stage == "discovering",
                discovery_analysis_sessions.c.discovery_lease_expires_at < now,
                discovery_analysis_sessions.c.provider_request_started_at.is_(None),
            ),
        )
        eligible_exists = exists(
            select(discovery_analysis_sessions.c.id).where(
                discovery_analysis_sessions.c.workspace_id == workspaces.c.id,
                discovery_analysis_sessions.c.cancellation_requested.is_(False),
                eligible_job,
            )
        )
        live_count = (
            select(func.count())
            .select_from(discovery_analysis_sessions)
            .where(
                discovery_analysis_sessions.c.workspace_id == workspaces.c.id,
                discovery_analysis_sessions.c.status == "running",
                discovery_analysis_sessions.c.job_stage == "discovering",
                discovery_analysis_sessions.c.discovery_lease_expires_at >= now,
            )
            .correlate(workspaces)
            .scalar_subquery()
        )
        oldest_created_at = (
            select(func.min(discovery_analysis_sessions.c.created_at))
            .where(
                discovery_analysis_sessions.c.workspace_id == workspaces.c.id,
                discovery_analysis_sessions.c.cancellation_requested.is_(False),
                eligible_job,
            )
            .correlate(workspaces)
            .scalar_subquery()
        )
        async with self._engine.begin() as connection:
            workspace_id = await connection.scalar(
                select(workspaces.c.id)
                .where(
                    eligible_exists,
                    live_count < workspaces.c.discovery_concurrency_limit,
                )
                .order_by(oldest_created_at, workspaces.c.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if workspace_id is None:
                return None

            rechecked_live_count = await connection.scalar(
                select(func.count())
                .select_from(discovery_analysis_sessions)
                .where(
                    discovery_analysis_sessions.c.workspace_id == workspace_id,
                    discovery_analysis_sessions.c.status == "running",
                    discovery_analysis_sessions.c.job_stage == "discovering",
                    discovery_analysis_sessions.c.discovery_lease_expires_at >= now,
                )
            )
            concurrency_limit = await connection.scalar(
                select(workspaces.c.discovery_concurrency_limit).where(
                    workspaces.c.id == workspace_id
                )
            )
            if (
                rechecked_live_count is None
                or concurrency_limit is None
                or rechecked_live_count >= concurrency_limit
            ):
                return None

            claimable = (
                select(discovery_analysis_sessions)
                .where(
                    discovery_analysis_sessions.c.workspace_id == workspace_id,
                    discovery_analysis_sessions.c.cancellation_requested.is_(False),
                    eligible_job,
                )
                .order_by(
                    discovery_analysis_sessions.c.created_at,
                    discovery_analysis_sessions.c.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            claimable_row = (await connection.execute(claimable)).mappings().one_or_none()
            if claimable_row is None:
                return None
            claimed = (
                (
                    await connection.execute(
                        update(discovery_analysis_sessions)
                        .where(
                            discovery_analysis_sessions.c.workspace_id == workspace_id,
                            discovery_analysis_sessions.c.id == claimable_row.id,
                        )
                        .values(
                            status="running",
                            job_stage="discovering",
                            discovery_claimed_by=worker_id,
                            discovery_lease_expires_at=lease_expires_at,
                            updated_at=now,
                        )
                        .returning(*discovery_analysis_sessions.c)
                    )
                )
                .mappings()
                .one()
            )
            await self._insert_event(
                connection,
                workspace_id,
                claimed.id,
                None,
                "job_claimed",
                now,
                actor_kind="worker",
            )
        return self._claim_from_row(claimed)

    async def renew_discovery_lease(
        self,
        session_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        if lease_expires_at <= now:
            raise ValueError("A renewed discovery lease must be in the future.")
        statement = (
            update(discovery_analysis_sessions)
            .where(
                discovery_analysis_sessions.c.id == session_id,
                discovery_analysis_sessions.c.status == "running",
                discovery_analysis_sessions.c.job_stage == "discovering",
                discovery_analysis_sessions.c.discovery_claimed_by == worker_id,
                discovery_analysis_sessions.c.discovery_lease_expires_at >= now,
            )
            .values(discovery_lease_expires_at=lease_expires_at, updated_at=now)
            .returning(discovery_analysis_sessions.c.id)
        )
        async with self._engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def mark_provider_request_started(
        self,
        session_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> bool:
        async with self._engine.begin() as connection:
            started = (
                await connection.execute(
                    update(discovery_analysis_sessions)
                    .where(
                        discovery_analysis_sessions.c.id == session_id,
                        discovery_analysis_sessions.c.status == "running",
                        discovery_analysis_sessions.c.job_stage == "discovering",
                        discovery_analysis_sessions.c.discovery_claimed_by == worker_id,
                        discovery_analysis_sessions.c.discovery_lease_expires_at >= now,
                        discovery_analysis_sessions.c.provider_request_started_at.is_(None),
                        discovery_analysis_sessions.c.cancellation_requested.is_(False),
                    )
                    .values(provider_request_started_at=now, updated_at=now)
                    .returning(
                        discovery_analysis_sessions.c.id,
                        discovery_analysis_sessions.c.workspace_id,
                    )
                )
            ).one_or_none()
            if started is None:
                return False
            await self._insert_event(
                connection,
                started.workspace_id,
                started.id,
                None,
                "provider_request_started",
                now,
                actor_kind="worker",
            )
            return True

    async def is_discovery_cancellation_requested(
        self,
        session_id: UUID,
        worker_id: str,
    ) -> bool:
        async with self._engine.connect() as connection:
            requested = await connection.scalar(
                select(discovery_analysis_sessions.c.cancellation_requested).where(
                    discovery_analysis_sessions.c.id == session_id,
                    discovery_analysis_sessions.c.status == "running",
                    discovery_analysis_sessions.c.job_stage == "discovering",
                    discovery_analysis_sessions.c.discovery_claimed_by == worker_id,
                )
            )
        return requested is True

    async def complete_discovery(
        self,
        claim: DiscoveryClaim,
        result: ScopedDiscoveryResult,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        worker_id = claim.session.discovery_claimed_by
        if worker_id is None:
            return False
        async with self._engine.begin() as connection:
            owned = (
                (
                    await connection.execute(
                        select(discovery_analysis_sessions)
                        .where(
                            discovery_analysis_sessions.c.workspace_id
                            == claim.session.workspace_id,
                            discovery_analysis_sessions.c.id == claim.session.id,
                            discovery_analysis_sessions.c.status == "running",
                            discovery_analysis_sessions.c.job_stage == "discovering",
                            discovery_analysis_sessions.c.discovery_claimed_by == worker_id,
                            discovery_analysis_sessions.c.discovery_lease_expires_at >= now,
                            discovery_analysis_sessions.c.provider_request_started_at.is_not(None),
                            discovery_analysis_sessions.c.cancellation_requested.is_(False),
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if owned is None:
                return False
            candidate_values = [
                {
                    "id": uuid4(),
                    "workspace_id": claim.session.workspace_id,
                    "session_id": claim.session.id,
                    "ordinal": ordinal,
                    "source_url": str(candidate.url),
                    "title": candidate.title,
                    "description": candidate.description,
                    "document_type": candidate.document_type.value,
                    "status": CandidateAnalysisStatus.QUEUED.value,
                    "attempt_count": 0,
                    "available_at": now,
                    "claimed_by": None,
                    "lease_expires_at": None,
                    "bytes_downloaded": 0,
                    "content_length": None,
                    "sha256": None,
                    "media_type": None,
                    "safe_filename": None,
                    "page_count": None,
                    "analyzed_page_count": 0,
                    "table_count": 0,
                    "table_count_lower_bound": False,
                    "preview_page_num": None,
                    "preview_width": None,
                    "preview_height": None,
                    "error_code": None,
                    "error_detail": None,
                    "error_retryable": None,
                    "promoted_document_id": None,
                    "created_at": now,
                    "updated_at": now,
                    "started_at": None,
                    "completed_at": None,
                    "expires_at": expires_at,
                }
                for ordinal, candidate in enumerate(result.response.candidates)
            ]
            if candidate_values:
                await connection.execute(insert(candidate_analyses).values(candidate_values))
            terminal = not candidate_values
            retained_until = now + (claim.session.expires_at - claim.session.created_at)
            cache_deadline = (
                min(now + _CACHE_REUSE_DURATION, retained_until)
                if terminal
                else None
            )
            await connection.execute(
                update(discovery_analysis_sessions)
                .where(
                    discovery_analysis_sessions.c.workspace_id == claim.session.workspace_id,
                    discovery_analysis_sessions.c.id == claim.session.id,
                )
                .values(
                    provider_search_ids=list(result.response.provider_search_ids),
                    candidate_count=len(candidate_values),
                    status="completed" if terminal else "running",
                    job_stage="completed" if terminal else "analyzing",
                    discovery_claimed_by=None,
                    discovery_lease_expires_at=None,
                    cache_reusable_until=cache_deadline,
                    expires_at=retained_until if terminal else expires_at,
                    completed_at=now if terminal else None,
                    updated_at=now,
                )
            )
            await self._insert_event(
                connection,
                claim.session.workspace_id,
                claim.session.id,
                None,
                "job_completed" if terminal else "analysis_started",
                now,
                actor_kind="worker",
            )
        return True

    async def fail_discovery(
        self,
        session_id: UUID,
        worker_id: str,
        code: str,
        now: datetime,
    ) -> bool:
        if code not in _PUBLIC_FAILURE_CODES:
            raise ValueError("The discovery failure code is not public-safe.")
        async with self._engine.begin() as connection:
            failed = (
                await connection.execute(
                    update(discovery_analysis_sessions)
                    .where(
                        discovery_analysis_sessions.c.id == session_id,
                        discovery_analysis_sessions.c.status == "running",
                        discovery_analysis_sessions.c.job_stage == "discovering",
                        discovery_analysis_sessions.c.discovery_claimed_by == worker_id,
                    )
                    .values(
                        status="failed",
                        job_stage="failed",
                        discovery_claimed_by=None,
                        discovery_lease_expires_at=None,
                        cache_reusable_until=None,
                        error_code=code,
                        error_detail=None,
                        completed_at=now,
                        updated_at=now,
                    )
                    .returning(
                        discovery_analysis_sessions.c.id,
                        discovery_analysis_sessions.c.workspace_id,
                    )
                )
            ).one_or_none()
            if failed is None:
                return False
            await self._insert_event(
                connection,
                failed.workspace_id,
                failed.id,
                None,
                "job_failed",
                now,
                actor_kind="worker",
            )
            return True

    async def cancel_discovery(
        self,
        session_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> bool:
        async with self._engine.begin() as connection:
            cancelled = (
                await connection.execute(
                    update(discovery_analysis_sessions)
                    .where(
                        discovery_analysis_sessions.c.id == session_id,
                        discovery_analysis_sessions.c.status == "running",
                        discovery_analysis_sessions.c.job_stage == "discovering",
                        discovery_analysis_sessions.c.discovery_claimed_by == worker_id,
                        discovery_analysis_sessions.c.cancellation_requested.is_(True),
                    )
                    .values(
                        status="cancelled",
                        job_stage="cancelled",
                        discovery_claimed_by=None,
                        discovery_lease_expires_at=None,
                        cache_reusable_until=None,
                        expires_at=now,
                        completed_at=now,
                        updated_at=now,
                    )
                    .returning(
                        discovery_analysis_sessions.c.id,
                        discovery_analysis_sessions.c.workspace_id,
                    )
                )
            ).one_or_none()
            if cancelled is None:
                return False
            await self._insert_event(
                connection,
                cancelled.workspace_id,
                cancelled.id,
                None,
                "job_cancelled",
                now,
                actor_kind="worker",
            )
            return True

    async def recover_uncertain_discovery(self, now: datetime) -> bool:
        async with self._engine.begin() as connection:
            uncertain = (
                (
                    await connection.execute(
                        select(discovery_analysis_sessions)
                        .where(
                            discovery_analysis_sessions.c.status == "running",
                            discovery_analysis_sessions.c.job_stage == "discovering",
                            discovery_analysis_sessions.c.discovery_lease_expires_at < now,
                            discovery_analysis_sessions.c.provider_request_started_at.is_not(None),
                        )
                        .order_by(
                            discovery_analysis_sessions.c.discovery_lease_expires_at,
                            discovery_analysis_sessions.c.id,
                        )
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if uncertain is None:
                return False
            await connection.execute(
                update(discovery_analysis_sessions)
                .where(
                    discovery_analysis_sessions.c.workspace_id == uncertain.workspace_id,
                    discovery_analysis_sessions.c.id == uncertain.id,
                )
                .values(
                    status="failed",
                    job_stage="failed",
                    discovery_claimed_by=None,
                    discovery_lease_expires_at=None,
                    cache_reusable_until=None,
                    error_code="provider_outcome_unknown",
                    error_detail=None,
                    completed_at=now,
                    updated_at=now,
                )
            )
            await self._insert_event(
                connection,
                uncertain.workspace_id,
                uncertain.id,
                None,
                "job_failed",
                now,
                actor_kind="system",
            )
            return True

    @staticmethod
    def _claim_from_row(row: RowMapping) -> DiscoveryClaim:
        if row.result_limit is None or row.request_fingerprint is None:
            raise RuntimeError("A queued discovery job is not reconstructable.")
        selection = DiscoverySelection(
            connection_id=row.firecrawl_connection_id,
            connection_name=row.firecrawl_connection_name_snapshot,
            connection_type=(
                ConnectionType(row.firecrawl_connection_type_snapshot)
                if row.firecrawl_connection_type_snapshot is not None
                else None
            ),
            credential_revision=row.firecrawl_credential_revision_snapshot,
            provider_identity="",
        )
        request = DurableAnalysisSearchRequest(
            query=row.query,
            limit=row.result_limit,
            document_types=tuple(row.document_types),
            include_domains=tuple(row.include_domains),
            exclude_domains=tuple(row.exclude_domains),
            firecrawl_connection_id=row.firecrawl_connection_id,
            tables_required=row.tables_required,
            force_refresh=False,
        )
        return DiscoveryClaim(session_record_from_row(row), request, selection)

    async def update_policy(
        self,
        workspace_id: UUID,
        concurrency_limit: int,
        actor_user_id: UUID,
        now: datetime,
    ) -> DiscoveryPolicyRecord:
        if not 1 <= concurrency_limit <= 5:
            raise ValueError("Discovery concurrency limit must be between 1 and 5.")
        async with self._engine.begin() as connection:
            old_limit = await connection.scalar(
                select(workspaces.c.discovery_concurrency_limit)
                .where(workspaces.c.id == workspace_id)
                .with_for_update()
            )
            if old_limit is None:
                raise LookupError("The discovery workspace policy is unavailable.")
            await connection.execute(
                update(workspaces)
                .where(workspaces.c.id == workspace_id)
                .values(discovery_concurrency_limit=concurrency_limit, updated_at=now)
            )
            await self._insert_event(
                connection,
                workspace_id,
                None,
                actor_user_id,
                "concurrency_updated",
                now,
                {"old": old_limit, "new": concurrency_limit},
            )
        return DiscoveryPolicyRecord(workspace_id, concurrency_limit)

    @staticmethod
    def _validate_submission(
        request: DurableAnalysisSearchRequest,
        fingerprint: str,
        now: datetime,
        expires_at: datetime,
        byte_limit: int,
        reason: DiscoveryCreationReason,
        parent_session_id: UUID | None,
    ) -> None:
        if _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
            raise ValueError("The request fingerprint must be 64 lowercase hexadecimal characters.")
        if byte_limit <= 0:
            raise ValueError("The discovery session byte limit must be positive.")
        if expires_at <= now:
            raise ValueError("The discovery session expiration must be in the future.")
        if reason is DiscoveryCreationReason.INITIAL and (
            request.force_refresh or parent_session_id is not None
        ):
            raise ValueError("An initial submission cannot be forced or linked to a parent.")
        if reason is DiscoveryCreationReason.RETRY and (
            request.force_refresh or parent_session_id is None
        ):
            raise ValueError("A retry must be non-forced and linked to a parent.")
        if reason is DiscoveryCreationReason.FORCE_REFRESH and not request.force_refresh:
            raise ValueError("A force-refresh reason requires a forced request.")

    @staticmethod
    async def _require_eligible_parent(
        connection: AsyncConnection,
        workspace_id: UUID,
        parent_session_id: UUID,
        fingerprint: str,
        reason: DiscoveryCreationReason,
        now: datetime,
    ) -> None:
        conditions = [
            discovery_analysis_sessions.c.workspace_id == workspace_id,
            discovery_analysis_sessions.c.id == parent_session_id,
            discovery_analysis_sessions.c.request_fingerprint == fingerprint,
        ]
        if reason is DiscoveryCreationReason.RETRY:
            conditions.extend(
                (
                    discovery_analysis_sessions.c.status.in_(("failed", "cancelled")),
                    discovery_analysis_sessions.c.result_limit.is_not(None),
                )
            )
        elif reason is DiscoveryCreationReason.FORCE_REFRESH:
            conditions.extend(
                (
                    discovery_analysis_sessions.c.status == "completed",
                    discovery_analysis_sessions.c.expires_at > now,
                )
            )
        else:
            raise ValueError("Only retries and forced refreshes may link a parent session.")

        eligible_parent = await connection.scalar(
            select(discovery_analysis_sessions.c.id).where(*conditions)
        )
        if eligible_parent is None:
            raise LookupError("The parent discovery session is unavailable.")

    @staticmethod
    async def _matching_active(
        connection: AsyncConnection,
        workspace_id: UUID,
        fingerprint: str,
    ) -> RowMapping | None:
        return (
            (
                await connection.execute(
                    select(discovery_analysis_sessions)
                    .where(
                        discovery_analysis_sessions.c.workspace_id == workspace_id,
                        discovery_analysis_sessions.c.request_fingerprint == fingerprint,
                        discovery_analysis_sessions.c.status.in_(("queued", "running")),
                    )
                    .order_by(
                        discovery_analysis_sessions.c.created_at.desc(),
                        discovery_analysis_sessions.c.id.desc(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )

    @staticmethod
    async def _matching_completed(
        connection: AsyncConnection,
        workspace_id: UUID,
        fingerprint: str,
        now: datetime,
        *,
        reusable_only: bool,
    ) -> RowMapping | None:
        conditions = [
            discovery_analysis_sessions.c.workspace_id == workspace_id,
            discovery_analysis_sessions.c.request_fingerprint == fingerprint,
            discovery_analysis_sessions.c.status == AnalysisSessionStatus.COMPLETED.value,
            discovery_analysis_sessions.c.expires_at > now,
        ]
        if reusable_only:
            conditions.extend(
                (
                    discovery_analysis_sessions.c.cache_reusable_until.is_not(None),
                    discovery_analysis_sessions.c.cache_reusable_until > now,
                )
            )
        return (
            (
                await connection.execute(
                    select(discovery_analysis_sessions)
                    .where(*conditions)
                    .order_by(
                        discovery_analysis_sessions.c.completed_at.desc(),
                        discovery_analysis_sessions.c.id.desc(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )

    @staticmethod
    async def _insert_event(
        connection: AsyncConnection,
        workspace_id: UUID,
        session_id: UUID | None,
        actor_user_id: UUID | None,
        event_type: str,
        now: datetime,
        safe_metadata: dict[str, object] | None = None,
        *,
        actor_kind: str = "user",
    ) -> None:
        metadata = {} if safe_metadata is None else safe_metadata
        expected_keys = _EVENT_METADATA_KEYS.get(event_type)
        if expected_keys is None or metadata.keys() != expected_keys:
            raise ValueError("Discovery audit metadata does not match its event allow-list.")
        if event_type == "concurrency_updated" and any(
            type(value) is not int or not 1 <= value <= 5 for value in metadata.values()
        ):
            raise ValueError("Discovery concurrency audit values must be integers from 1 to 5.")
        if actor_kind not in {"user", "worker", "system"}:
            raise ValueError("The discovery audit actor kind is invalid.")
        await connection.execute(
            insert(discovery_job_events).values(
                id=uuid4(),
                workspace_id=workspace_id,
                session_id=session_id,
                actor_kind=actor_kind,
                actor_user_id=actor_user_id,
                event_type=event_type,
                safe_metadata=metadata,
                created_at=now,
            )
        )

    @staticmethod
    def _constraint_name(error: IntegrityError) -> str | None:
        diagnostic = getattr(error.orig, "diag", None)
        return getattr(diagnostic, "constraint_name", None)
