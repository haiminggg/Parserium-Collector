from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, and_, delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from parserium_collector.adapters.database.tables import (
    artifact_references,
    candidate_analyses,
    candidate_tables,
    collection_jobs,
    discovery_analysis_sessions,
    discovery_job_events,
    documents,
)
from parserium_collector.features.acquisition.models import (
    CollectionJobRecord,
    CollectionJobStatus,
)
from parserium_collector.features.analysis.models import (
    AnalysisProgress,
    AnalysisSearchRequest,
    AnalysisSessionRecord,
    AnalysisSessionStatus,
    CandidateAnalysisRecord,
    CandidateAnalysisStatus,
    CandidateTableInput,
    CandidateTableRecord,
    DiscoveryCreationReason,
    DiscoveryJobStage,
    TableBoundingBox,
)
from parserium_collector.features.discovery.models import DocumentType
from parserium_collector.features.discovery.scoped import ScopedDiscoveryResult
from parserium_collector.features.firecrawl_connections.models import ConnectionType
from parserium_collector.features.identity.models import WorkspaceScope
from parserium_collector.features.storage.models import ArtifactKind, ArtifactLifecycle
from parserium_collector.features.storage.repository import PostgresArtifactRepository

ACTIVE_CANDIDATE_STATUSES = (
    CandidateAnalysisStatus.DOWNLOADING,
    CandidateAnalysisStatus.VALIDATING,
    CandidateAnalysisStatus.CONVERTING,
    CandidateAnalysisStatus.PARSING,
)
TERMINAL_CANDIDATE_STATUSES = (
    CandidateAnalysisStatus.READY,
    CandidateAnalysisStatus.NO_TABLES,
    CandidateAnalysisStatus.PARTIAL,
    CandidateAnalysisStatus.FAILED,
    CandidateAnalysisStatus.CANCELLED,
    CandidateAnalysisStatus.PROMOTED,
)
LEGAL_STAGE_TRANSITIONS = {
    (CandidateAnalysisStatus.DOWNLOADING, CandidateAnalysisStatus.VALIDATING),
    (CandidateAnalysisStatus.VALIDATING, CandidateAnalysisStatus.CONVERTING),
    (CandidateAnalysisStatus.VALIDATING, CandidateAnalysisStatus.PARSING),
    (CandidateAnalysisStatus.CONVERTING, CandidateAnalysisStatus.PARSING),
}


def session_record_from_row(row: RowMapping) -> AnalysisSessionRecord:
    return AnalysisSessionRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        created_by_user_id=row.created_by_user_id,
        firecrawl_connection_id=row.firecrawl_connection_id,
        firecrawl_connection_name_snapshot=row.firecrawl_connection_name_snapshot,
        firecrawl_connection_type_snapshot=(
            ConnectionType(row.firecrawl_connection_type_snapshot)
            if row.firecrawl_connection_type_snapshot is not None
            else None
        ),
        query=row.query,
        document_types=tuple(DocumentType(value) for value in row.document_types),
        include_domains=tuple(row.include_domains),
        exclude_domains=tuple(row.exclude_domains),
        tables_required=row.tables_required,
        provider_search_ids=tuple(row.provider_search_ids),
        status=AnalysisSessionStatus(row.status),
        candidate_count=row.candidate_count,
        session_byte_limit=row.session_byte_limit,
        bytes_downloaded=row.bytes_downloaded,
        cancellation_requested=row.cancellation_requested,
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
        completed_at=row.completed_at,
        error_code=row.error_code,
        error_detail=row.error_detail,
        request_fingerprint=row.request_fingerprint,
        request_fingerprint_version=row.request_fingerprint_version,
        result_limit=row.result_limit,
        job_stage=DiscoveryJobStage(row.job_stage),
        cache_reusable_until=row.cache_reusable_until,
        discovery_claimed_by=row.discovery_claimed_by,
        discovery_lease_expires_at=row.discovery_lease_expires_at,
        provider_request_started_at=row.provider_request_started_at,
        firecrawl_credential_revision_snapshot=row.firecrawl_credential_revision_snapshot,
        creation_reason=DiscoveryCreationReason(row.creation_reason),
    )


def _candidate_record(row: RowMapping) -> CandidateAnalysisRecord:
    return CandidateAnalysisRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        session_id=row.session_id,
        ordinal=row.ordinal,
        source_url=row.source_url,
        title=row.title,
        description=row.description,
        document_type=DocumentType(row.document_type),
        status=CandidateAnalysisStatus(row.status),
        attempt_count=row.attempt_count,
        available_at=row.available_at,
        claimed_by=row.claimed_by,
        lease_expires_at=row.lease_expires_at,
        bytes_downloaded=row.bytes_downloaded,
        content_length=row.content_length,
        sha256=row.sha256,
        media_type=row.media_type,
        safe_filename=row.safe_filename,
        page_count=row.page_count,
        analyzed_page_count=row.analyzed_page_count,
        table_count=row.table_count,
        table_count_lower_bound=row.table_count_lower_bound,
        preview_page_num=row.preview_page_num,
        preview_width=row.preview_width,
        preview_height=row.preview_height,
        error_code=row.error_code,
        error_detail=row.error_detail,
        error_retryable=row.error_retryable,
        promoted_document_id=row.promoted_document_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        expires_at=row.expires_at,
    )


def _table_record(row: RowMapping) -> CandidateTableRecord:
    return CandidateTableRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        candidate_analysis_id=row.candidate_analysis_id,
        page_num=row.page_num,
        table_index=row.table_index,
        bounding_box=TableBoundingBox(
            x=float(row.x),
            y=float(row.y),
            width=float(row.width),
            height=float(row.height),
        ),
        cells=tuple(tuple(str(cell) for cell in cells) for cells in row.cells),
        markdown=row.markdown,
        created_at=row.created_at,
    )


def _collection_job_record(row: RowMapping) -> CollectionJobRecord:
    return CollectionJobRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        source_url=row.source_url,
        title=row.title,
        expected_document_type=DocumentType(row.expected_document_type),
        status=CollectionJobStatus(row.status),
        attempt_count=row.attempt_count,
        available_at=row.available_at,
        claimed_by=row.claimed_by,
        lease_expires_at=row.lease_expires_at,
        bytes_downloaded=row.bytes_downloaded,
        content_length=row.content_length,
        document_id=row.document_id,
        error_code=row.error_code,
        error_detail=row.error_detail,
        error_retryable=row.error_retryable,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


class AnalysisRepository(Protocol):
    async def create_analysis_session(
        self,
        scope: WorkspaceScope,
        request: AnalysisSearchRequest,
        result: ScopedDiscoveryResult,
        now: datetime,
        expires_at: datetime,
        byte_limit: int,
    ) -> tuple[AnalysisSessionRecord, tuple[CandidateAnalysisRecord, ...]]: ...

    async def get_analysis_session(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> AnalysisSessionRecord | None: ...

    async def list_candidate_analyses(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> tuple[CandidateAnalysisRecord, ...]: ...

    async def get_workspace_candidate_analysis(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> CandidateAnalysisRecord | None: ...

    async def claim_candidate_analysis(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CandidateAnalysisRecord | None: ...

    async def claim_expired_candidate_cleanup(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CandidateAnalysisRecord | None: ...

    async def delete_expired_candidate_cleanup(
        self,
        candidate_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> bool: ...

    async def renew_candidate_lease(
        self,
        candidate_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool: ...

    async def update_candidate_progress(
        self,
        candidate_id: UUID,
        worker_id: str,
        bytes_downloaded: int,
        content_length: int | None,
        now: datetime,
    ) -> bool: ...

    async def transition_candidate_stage(
        self,
        candidate_id: UUID,
        worker_id: str,
        expected_status: CandidateAnalysisStatus,
        next_status: CandidateAnalysisStatus,
        now: datetime,
    ) -> bool: ...

    async def complete_candidate_analysis(
        self,
        candidate_id: UUID,
        worker_id: str,
        *,
        status: CandidateAnalysisStatus,
        sha256: str,
        media_type: str,
        safe_filename: str,
        artifacts: Sequence[tuple[UUID, ArtifactKind]],
        page_count: int,
        analyzed_page_count: int,
        table_count_lower_bound: bool,
        preview_page_num: int | None,
        preview_width: int | None,
        preview_height: int | None,
        tables: Sequence[CandidateTableInput],
        now: datetime,
    ) -> CandidateAnalysisRecord: ...

    async def fail_candidate_analysis(
        self,
        candidate_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        error_detail: str,
        retryable: bool,
        retry_at: datetime | None,
        now: datetime,
    ) -> bool: ...

    async def cancel_analysis_session(
        self,
        workspace_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def list_candidate_tables(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> tuple[CandidateTableRecord, ...]: ...

    async def mark_candidate_promoted(
        self,
        candidate_id: UUID,
        document_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def promote_candidate_to_collection(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        *,
        document_id: UUID,
        artifact_object_id: UUID,
        size_bytes: int,
        now: datetime,
    ) -> CollectionJobRecord | None: ...

    async def get_analysis_progress(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> AnalysisProgress | None: ...


class PostgresAnalysisRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        artifact_repository: PostgresArtifactRepository | None = None,
    ) -> None:
        self._engine = engine
        self._artifact_repository = artifact_repository or PostgresArtifactRepository(engine)

    async def create_analysis_session(
        self,
        scope: WorkspaceScope,
        request: AnalysisSearchRequest,
        result: ScopedDiscoveryResult,
        now: datetime,
        expires_at: datetime,
        byte_limit: int,
    ) -> tuple[AnalysisSessionRecord, tuple[CandidateAnalysisRecord, ...]]:
        session_id = uuid4()
        candidate_values = [
            {
                "id": uuid4(),
                "workspace_id": scope.workspace_id,
                "session_id": session_id,
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
        terminal = len(candidate_values) == 0
        session_values = {
            "id": session_id,
            "workspace_id": scope.workspace_id,
            "created_by_user_id": scope.user_id,
            "owner_session_digest": None,
            "firecrawl_connection_id": result.connection_id,
            "firecrawl_connection_name_snapshot": result.connection_name_snapshot,
            "firecrawl_connection_type_snapshot": (
                result.connection_type_snapshot.value
                if result.connection_type_snapshot is not None
                else None
            ),
            "query": request.query,
            "document_types": [value.value for value in request.document_types],
            "include_domains": list(request.include_domains),
            "exclude_domains": list(request.exclude_domains),
            "tables_required": request.tables_required,
            "provider_search_ids": list(result.response.provider_search_ids),
            "status": (
                AnalysisSessionStatus.COMPLETED.value
                if terminal
                else AnalysisSessionStatus.RUNNING.value
            ),
            "job_stage": "completed" if terminal else "analyzing",
            "creation_reason": "initial",
            "request_fingerprint": None,
            "request_fingerprint_version": None,
            "result_limit": request.limit,
            "cache_reusable_until": None,
            "discovery_claimed_by": None,
            "discovery_lease_expires_at": None,
            "provider_request_started_at": None,
            "firecrawl_credential_revision_snapshot": None,
            "candidate_count": len(candidate_values),
            "session_byte_limit": byte_limit,
            "bytes_downloaded": 0,
            "cancellation_requested": False,
            "error_code": None,
            "error_detail": None,
            "created_at": now,
            "updated_at": now,
            "expires_at": expires_at,
            "completed_at": now if terminal else None,
        }
        async with self._engine.begin() as connection:
            session_row = (
                (
                    await connection.execute(
                        insert(discovery_analysis_sessions)
                        .values(**session_values)
                        .returning(*discovery_analysis_sessions.c)
                    )
                )
                .mappings()
                .one()
            )
            candidate_rows: Sequence[RowMapping] = ()
            if candidate_values:
                candidate_rows = (
                    (
                        await connection.execute(
                            insert(candidate_analyses)
                            .values(candidate_values)
                            .returning(*candidate_analyses.c)
                        )
                    )
                    .mappings()
                    .all()
                )
        return session_record_from_row(session_row), tuple(
            _candidate_record(row) for row in candidate_rows
        )

    async def get_analysis_session(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> AnalysisSessionRecord | None:
        statement = select(discovery_analysis_sessions).where(
            discovery_analysis_sessions.c.id == session_id,
            discovery_analysis_sessions.c.workspace_id == workspace_id,
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().one_or_none()
        return None if row is None else session_record_from_row(row)

    async def list_candidate_analyses(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> tuple[CandidateAnalysisRecord, ...]:
        statement = (
            select(candidate_analyses)
            .where(
                candidate_analyses.c.workspace_id == workspace_id,
                candidate_analyses.c.session_id == session_id,
            )
            .order_by(candidate_analyses.c.ordinal)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_candidate_record(row) for row in rows)

    async def get_workspace_candidate_analysis(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> CandidateAnalysisRecord | None:
        statement = (
            select(candidate_analyses)
            .join(
                discovery_analysis_sessions,
                discovery_analysis_sessions.c.id == candidate_analyses.c.session_id,
            )
            .where(
                candidate_analyses.c.id == candidate_id,
                candidate_analyses.c.workspace_id == workspace_id,
                discovery_analysis_sessions.c.workspace_id == workspace_id,
            )
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().one_or_none()
        return None if row is None else _candidate_record(row)

    async def claim_candidate_analysis(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CandidateAnalysisRecord | None:
        eligible = or_(
            and_(
                candidate_analyses.c.status == CandidateAnalysisStatus.QUEUED.value,
                candidate_analyses.c.available_at <= now,
            ),
            and_(
                candidate_analyses.c.status.in_(
                    tuple(status.value for status in ACTIVE_CANDIDATE_STATUSES)
                ),
                candidate_analyses.c.lease_expires_at < now,
            ),
        )
        claimable = (
            select(candidate_analyses.c.id, candidate_analyses.c.session_id)
            .join(
                discovery_analysis_sessions,
                discovery_analysis_sessions.c.id == candidate_analyses.c.session_id,
            )
            .where(
                eligible,
                discovery_analysis_sessions.c.status.in_(
                    (
                        AnalysisSessionStatus.QUEUED.value,
                        AnalysisSessionStatus.RUNNING.value,
                    )
                ),
                discovery_analysis_sessions.c.cancellation_requested.is_(False),
            )
            .order_by(
                discovery_analysis_sessions.c.created_at,
                candidate_analyses.c.ordinal,
            )
            .with_for_update(skip_locked=True, of=candidate_analyses)
            .limit(1)
        )
        async with self._engine.begin() as connection:
            claim = (await connection.execute(claimable)).one_or_none()
            if claim is None:
                return None
            row = (
                (
                    await connection.execute(
                        update(candidate_analyses)
                        .where(candidate_analyses.c.id == claim.id)
                        .values(
                            status=CandidateAnalysisStatus.DOWNLOADING.value,
                            attempt_count=candidate_analyses.c.attempt_count + 1,
                            claimed_by=worker_id,
                            lease_expires_at=lease_expires_at,
                            error_code=None,
                            error_detail=None,
                            error_retryable=None,
                            updated_at=now,
                            started_at=now,
                            completed_at=None,
                        )
                        .returning(*candidate_analyses.c)
                    )
                )
                .mappings()
                .one()
            )
            await connection.execute(
                update(discovery_analysis_sessions)
                .where(discovery_analysis_sessions.c.id == claim.session_id)
                .values(
                    status=AnalysisSessionStatus.RUNNING.value,
                    job_stage="analyzing",
                    updated_at=now,
                )
            )
        return _candidate_record(row)

    async def claim_expired_candidate_cleanup(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CandidateAnalysisRecord | None:
        claimable = (
            select(candidate_analyses.c.id)
            .join(
                discovery_analysis_sessions,
                discovery_analysis_sessions.c.id == candidate_analyses.c.session_id,
            )
            .where(
                candidate_analyses.c.status.in_(
                    tuple(status.value for status in TERMINAL_CANDIDATE_STATUSES)
                ),
                candidate_analyses.c.expires_at <= now,
                discovery_analysis_sessions.c.status.not_in(
                    (
                        AnalysisSessionStatus.QUEUED.value,
                        AnalysisSessionStatus.RUNNING.value,
                    )
                ),
                or_(
                    candidate_analyses.c.claimed_by.is_(None),
                    candidate_analyses.c.lease_expires_at.is_(None),
                    candidate_analyses.c.lease_expires_at < now,
                ),
            )
            .order_by(candidate_analyses.c.expires_at, candidate_analyses.c.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        async with self._engine.begin() as connection:
            candidate_id = (await connection.execute(claimable)).scalar_one_or_none()
            if candidate_id is None:
                return None
            row = (
                (
                    await connection.execute(
                        update(candidate_analyses)
                        .where(candidate_analyses.c.id == candidate_id)
                        .values(
                            claimed_by=worker_id,
                            lease_expires_at=lease_expires_at,
                            updated_at=now,
                        )
                        .returning(*candidate_analyses.c)
                    )
                )
                .mappings()
                .one()
            )
        return _candidate_record(row)

    async def delete_expired_candidate_cleanup(
        self,
        candidate_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> bool:
        async with self._engine.begin() as connection:
            session_id = (
                await connection.execute(
                    delete(candidate_analyses)
                    .where(
                        candidate_analyses.c.id == candidate_id,
                        candidate_analyses.c.claimed_by == worker_id,
                        candidate_analyses.c.status.in_(
                            tuple(status.value for status in TERMINAL_CANDIDATE_STATUSES)
                        ),
                        candidate_analyses.c.expires_at <= now,
                    )
                    .returning(candidate_analyses.c.session_id)
                )
            ).scalar_one_or_none()
            if session_id is None:
                return False
            remaining = (
                await connection.execute(
                    select(func.count())
                    .select_from(candidate_analyses)
                    .where(candidate_analyses.c.session_id == session_id)
                )
            ).scalar_one()
            if remaining == 0:
                await connection.execute(
                    delete(discovery_analysis_sessions).where(
                        discovery_analysis_sessions.c.id == session_id
                    )
                )
        return True

    async def renew_candidate_lease(
        self,
        candidate_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        statement = (
            update(candidate_analyses)
            .where(
                candidate_analyses.c.id == candidate_id,
                candidate_analyses.c.claimed_by == worker_id,
                candidate_analyses.c.status.in_(
                    tuple(status.value for status in ACTIVE_CANDIDATE_STATUSES)
                ),
                candidate_analyses.c.lease_expires_at >= now,
            )
            .values(lease_expires_at=lease_expires_at, updated_at=now)
            .returning(candidate_analyses.c.id)
        )
        async with self._engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def update_candidate_progress(
        self,
        candidate_id: UUID,
        worker_id: str,
        bytes_downloaded: int,
        content_length: int | None,
        now: datetime,
    ) -> bool:
        async with self._engine.begin() as connection:
            candidate = (
                (
                    await connection.execute(
                        select(candidate_analyses)
                        .where(
                            candidate_analyses.c.id == candidate_id,
                            candidate_analyses.c.claimed_by == worker_id,
                            candidate_analyses.c.status
                            == CandidateAnalysisStatus.DOWNLOADING.value,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if candidate is None or bytes_downloaded < candidate.bytes_downloaded:
                return False
            delta = bytes_downloaded - candidate.bytes_downloaded
            session_updated = (
                await connection.execute(
                    update(discovery_analysis_sessions)
                    .where(
                        discovery_analysis_sessions.c.id == candidate.session_id,
                        discovery_analysis_sessions.c.bytes_downloaded + delta
                        <= discovery_analysis_sessions.c.session_byte_limit,
                    )
                    .values(
                        bytes_downloaded=discovery_analysis_sessions.c.bytes_downloaded + delta,
                        updated_at=now,
                    )
                    .returning(discovery_analysis_sessions.c.id)
                )
            ).scalar_one_or_none()
            if session_updated is None:
                return False
            await connection.execute(
                update(candidate_analyses)
                .where(candidate_analyses.c.id == candidate_id)
                .values(
                    bytes_downloaded=bytes_downloaded,
                    content_length=content_length,
                    updated_at=now,
                )
            )
        return True

    async def transition_candidate_stage(
        self,
        candidate_id: UUID,
        worker_id: str,
        expected_status: CandidateAnalysisStatus,
        next_status: CandidateAnalysisStatus,
        now: datetime,
    ) -> bool:
        if (expected_status, next_status) not in LEGAL_STAGE_TRANSITIONS:
            return False
        statement = (
            update(candidate_analyses)
            .where(
                candidate_analyses.c.id == candidate_id,
                candidate_analyses.c.claimed_by == worker_id,
                candidate_analyses.c.status == expected_status.value,
            )
            .values(status=next_status.value, updated_at=now)
            .returning(candidate_analyses.c.id)
        )
        async with self._engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def complete_candidate_analysis(
        self,
        candidate_id: UUID,
        worker_id: str,
        *,
        status: CandidateAnalysisStatus,
        sha256: str,
        media_type: str,
        safe_filename: str,
        artifacts: Sequence[tuple[UUID, ArtifactKind]],
        page_count: int,
        analyzed_page_count: int,
        table_count_lower_bound: bool,
        preview_page_num: int | None,
        preview_width: int | None,
        preview_height: int | None,
        tables: Sequence[CandidateTableInput],
        now: datetime,
    ) -> CandidateAnalysisRecord:
        if status not in (
            CandidateAnalysisStatus.READY,
            CandidateAnalysisStatus.NO_TABLES,
            CandidateAnalysisStatus.PARTIAL,
        ):
            raise ValueError("Candidate completion requires a terminal analysis status.")
        table_values: list[dict[str, object]] = [
            {
                "id": uuid4(),
                "candidate_analysis_id": candidate_id,
                "page_num": table.page_num,
                "table_index": table.table_index,
                "x": table.bounding_box.x,
                "y": table.bounding_box.y,
                "width": table.bounding_box.width,
                "height": table.bounding_box.height,
                "cells": [list(row) for row in table.cells],
                "markdown": table.markdown,
                "created_at": now,
            }
            for table in tables
        ]
        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        update(candidate_analyses)
                        .where(
                            candidate_analyses.c.id == candidate_id,
                            candidate_analyses.c.claimed_by == worker_id,
                            candidate_analyses.c.status == CandidateAnalysisStatus.PARSING.value,
                        )
                        .values(
                            status=status.value,
                            sha256=sha256,
                            media_type=media_type,
                            safe_filename=safe_filename,
                            page_count=page_count,
                            analyzed_page_count=analyzed_page_count,
                            table_count=len(table_values),
                            table_count_lower_bound=table_count_lower_bound,
                            preview_page_num=preview_page_num,
                            preview_width=preview_width,
                            preview_height=preview_height,
                            claimed_by=None,
                            lease_expires_at=None,
                            updated_at=now,
                            completed_at=now,
                        )
                        .returning(*candidate_analyses.c)
                    )
                )
                .mappings()
                .one()
            )
            for artifact_object_id, kind in artifacts:
                await self._artifact_repository.finalize_reference_in_connection(
                    connection,
                    artifact_object_id,
                    workspace_id=row.workspace_id,
                    document_id=None,
                    candidate_analysis_id=candidate_id,
                    kind=kind,
                    lifecycle=ArtifactLifecycle.TEMPORARY,
                    expires_at=row.expires_at,
                    now=now,
                )
            await connection.execute(
                delete(candidate_tables).where(
                    candidate_tables.c.workspace_id == row.workspace_id,
                    candidate_tables.c.candidate_analysis_id == candidate_id,
                )
            )
            if table_values:
                for table_value in table_values:
                    table_value["workspace_id"] = row.workspace_id
                await connection.execute(insert(candidate_tables).values(table_values))
            await self._finish_session_if_terminal(connection, row.session_id, now)
        return _candidate_record(row)

    async def fail_candidate_analysis(
        self,
        candidate_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        error_detail: str,
        retryable: bool,
        retry_at: datetime | None,
        now: datetime,
    ) -> bool:
        queued = retry_at is not None
        candidate_values: dict[str, object] = {
            "status": (
                CandidateAnalysisStatus.QUEUED.value
                if queued
                else CandidateAnalysisStatus.FAILED.value
            ),
            "available_at": retry_at if retry_at is not None else now,
            "claimed_by": None,
            "lease_expires_at": None,
            "error_code": error_code,
            "error_detail": error_detail,
            "error_retryable": retryable,
            "updated_at": now,
            "completed_at": None if queued else now,
        }
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    update(candidate_analyses)
                    .where(
                        candidate_analyses.c.id == candidate_id,
                        candidate_analyses.c.claimed_by == worker_id,
                        candidate_analyses.c.status.in_(
                            tuple(status.value for status in ACTIVE_CANDIDATE_STATUSES)
                        ),
                    )
                    .values(**candidate_values)
                    .returning(candidate_analyses.c.session_id)
                )
            ).one_or_none()
            if row is None:
                return False
            if not queued:
                await connection.execute(
                    update(artifact_references)
                    .where(
                        artifact_references.c.candidate_analysis_id == candidate_id,
                        artifact_references.c.lifecycle == ArtifactLifecycle.TEMPORARY.value,
                        artifact_references.c.removed_at.is_(None),
                    )
                    .values(expires_at=now)
                )
                await self._finish_session_if_terminal(connection, row.session_id, now)
        return True

    async def cancel_analysis_session(
        self,
        workspace_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> bool:
        async with self._engine.begin() as connection:
            session = (
                await connection.execute(
                    select(discovery_analysis_sessions.c.id).where(
                        discovery_analysis_sessions.c.id == session_id,
                        discovery_analysis_sessions.c.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if session is None:
                return False
            session_candidate_ids = select(candidate_analyses.c.id).where(
                candidate_analyses.c.workspace_id == workspace_id,
                candidate_analyses.c.session_id == session_id,
            )
            await connection.execute(
                update(discovery_analysis_sessions)
                .where(
                    discovery_analysis_sessions.c.workspace_id == workspace_id,
                    discovery_analysis_sessions.c.id == session_id,
                )
                .values(
                    status=AnalysisSessionStatus.CANCELLED.value,
                    job_stage="cancelled",
                    cancellation_requested=True,
                    expires_at=now,
                    updated_at=now,
                    completed_at=now,
                )
            )
            await connection.execute(
                update(candidate_analyses)
                .where(
                    candidate_analyses.c.workspace_id == workspace_id,
                    candidate_analyses.c.session_id == session_id,
                )
                .values(expires_at=now, updated_at=now)
            )
            await connection.execute(
                update(candidate_analyses)
                .where(
                    candidate_analyses.c.workspace_id == workspace_id,
                    candidate_analyses.c.session_id == session_id,
                    candidate_analyses.c.status.in_(
                        (
                            CandidateAnalysisStatus.QUEUED.value,
                            *(status.value for status in ACTIVE_CANDIDATE_STATUSES),
                        )
                    ),
                )
                .values(
                    status=CandidateAnalysisStatus.CANCELLED.value,
                    claimed_by=None,
                    lease_expires_at=None,
                    updated_at=now,
                    completed_at=now,
                )
            )
            await connection.execute(
                update(artifact_references)
                .where(
                    artifact_references.c.workspace_id == workspace_id,
                    artifact_references.c.candidate_analysis_id.in_(session_candidate_ids),
                    artifact_references.c.lifecycle == ArtifactLifecycle.TEMPORARY.value,
                    artifact_references.c.removed_at.is_(None),
                )
                .values(expires_at=now)
            )
        return True

    async def list_candidate_tables(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> tuple[CandidateTableRecord, ...]:
        statement = (
            select(candidate_tables)
            .where(
                candidate_tables.c.workspace_id == workspace_id,
                candidate_tables.c.candidate_analysis_id == candidate_id,
            )
            .order_by(candidate_tables.c.page_num, candidate_tables.c.table_index)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_table_record(row) for row in rows)

    async def mark_candidate_promoted(
        self,
        candidate_id: UUID,
        document_id: UUID,
        now: datetime,
    ) -> bool:
        statement = (
            update(candidate_analyses)
            .where(
                candidate_analyses.c.id == candidate_id,
                candidate_analyses.c.status.in_(
                    (
                        CandidateAnalysisStatus.READY.value,
                        CandidateAnalysisStatus.NO_TABLES.value,
                        CandidateAnalysisStatus.PARTIAL.value,
                    )
                ),
            )
            .values(
                status=CandidateAnalysisStatus.PROMOTED.value,
                promoted_document_id=document_id,
                updated_at=now,
            )
            .returning(candidate_analyses.c.id)
        )
        async with self._engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def promote_candidate_to_collection(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        *,
        document_id: UUID,
        artifact_object_id: UUID,
        size_bytes: int,
        now: datetime,
    ) -> CollectionJobRecord | None:
        async with self._engine.begin() as connection:
            candidate = (
                (
                    await connection.execute(
                        select(
                            candidate_analyses,
                            discovery_analysis_sessions.c.created_by_user_id.label(
                                "session_created_by_user_id"
                            ),
                        )
                        .join(
                            discovery_analysis_sessions,
                            discovery_analysis_sessions.c.id == candidate_analyses.c.session_id,
                        )
                        .where(
                            candidate_analyses.c.id == candidate_id,
                            candidate_analyses.c.workspace_id == workspace_id,
                            discovery_analysis_sessions.c.workspace_id == workspace_id,
                            candidate_analyses.c.status.in_(
                                (
                                    CandidateAnalysisStatus.READY.value,
                                    CandidateAnalysisStatus.NO_TABLES.value,
                                    CandidateAnalysisStatus.PARTIAL.value,
                                )
                            ),
                            candidate_analyses.c.expires_at > now,
                            candidate_analyses.c.sha256.is_not(None),
                            candidate_analyses.c.media_type.is_not(None),
                            candidate_analyses.c.safe_filename.is_not(None),
                        )
                        .with_for_update(of=candidate_analyses)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if candidate is None:
                return None
            inserted_id = (
                await connection.execute(
                    postgresql_insert(documents)
                    .values(
                        id=document_id,
                        workspace_id=workspace_id,
                        sha256=candidate.sha256,
                        document_type=candidate.document_type,
                        media_type=candidate.media_type,
                        size_bytes=size_bytes,
                        safe_filename=candidate.safe_filename,
                        created_at=now,
                        deleted_at=None,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[documents.c.workspace_id, documents.c.sha256]
                    )
                    .returning(documents.c.id)
                )
            ).scalar_one_or_none()
            duplicate = inserted_id is None
            if inserted_id is None:
                inserted_id = (
                    await connection.execute(
                        select(documents.c.id).where(
                            documents.c.workspace_id == workspace_id,
                            documents.c.sha256 == candidate.sha256,
                            documents.c.deleted_at.is_(None),
                        )
                    )
                ).scalar_one()
                discarded = await self._artifact_repository.discard_upload_in_connection(
                    connection,
                    artifact_object_id,
                    workspace_id,
                    now=now,
                )
                if discarded is None:
                    raise LookupError("The duplicate promoted artifact does not exist.")
            else:
                await self._artifact_repository.finalize_reference_in_connection(
                    connection,
                    artifact_object_id,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    candidate_analysis_id=None,
                    kind=ArtifactKind.STORED_DOCUMENT,
                    lifecycle=ArtifactLifecycle.PERSISTENT,
                    expires_at=None,
                    now=now,
                )
            job_row = (
                (
                    await connection.execute(
                        insert(collection_jobs)
                        .values(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            created_by_user_id=candidate.session_created_by_user_id,
                            source_url=candidate.source_url,
                            title=candidate.title,
                            expected_document_type=candidate.document_type,
                            status=(
                                CollectionJobStatus.DUPLICATE.value
                                if duplicate
                                else CollectionJobStatus.COMPLETED.value
                            ),
                            attempt_count=0,
                            available_at=now,
                            claimed_by=None,
                            lease_expires_at=None,
                            bytes_downloaded=size_bytes,
                            content_length=size_bytes,
                            document_id=inserted_id,
                            error_code=None,
                            error_detail=None,
                            error_retryable=None,
                            created_at=now,
                            updated_at=now,
                            started_at=now,
                            completed_at=now,
                        )
                        .returning(*collection_jobs.c)
                    )
                )
                .mappings()
                .one()
            )
            await connection.execute(
                update(candidate_analyses)
                .where(
                    candidate_analyses.c.workspace_id == workspace_id,
                    candidate_analyses.c.id == candidate_id,
                    candidate_analyses.c.status.in_(
                        (
                            CandidateAnalysisStatus.READY.value,
                            CandidateAnalysisStatus.NO_TABLES.value,
                            CandidateAnalysisStatus.PARTIAL.value,
                        )
                    ),
                )
                .values(
                    status=CandidateAnalysisStatus.PROMOTED.value,
                    promoted_document_id=inserted_id,
                    updated_at=now,
                )
            )
        return _collection_job_record(job_row)

    async def get_analysis_progress(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> AnalysisProgress | None:
        session = await self.get_analysis_session(workspace_id, session_id)
        if session is None:
            return None
        statuses = [
            record.status for record in await self.list_candidate_analyses(workspace_id, session_id)
        ]
        return AnalysisProgress(
            session_id=session.id,
            candidate_count=session.candidate_count,
            terminal_count=sum(status in TERMINAL_CANDIDATE_STATUSES for status in statuses),
            ready_count=sum(
                status in (CandidateAnalysisStatus.READY, CandidateAnalysisStatus.PROMOTED)
                for status in statuses
            ),
            no_tables_count=statuses.count(CandidateAnalysisStatus.NO_TABLES),
            partial_count=statuses.count(CandidateAnalysisStatus.PARTIAL),
            failed_count=statuses.count(CandidateAnalysisStatus.FAILED),
            cancelled_count=statuses.count(CandidateAnalysisStatus.CANCELLED),
            bytes_downloaded=session.bytes_downloaded,
            session_byte_limit=session.session_byte_limit,
        )

    @staticmethod
    async def _finish_session_if_terminal(
        connection: AsyncConnection,
        session_id: UUID,
        now: datetime,
    ) -> None:
        active_count = (
            await connection.execute(
                select(func.count())
                .select_from(candidate_analyses)
                .where(
                    candidate_analyses.c.session_id == session_id,
                    candidate_analyses.c.status.not_in(
                        tuple(status.value for status in TERMINAL_CANDIDATE_STATUSES)
                    ),
                )
            )
        ).scalar_one()
        if active_count == 0:
            session = (
                await connection.execute(
                    select(
                        discovery_analysis_sessions.c.workspace_id,
                        discovery_analysis_sessions.c.created_at,
                        discovery_analysis_sessions.c.expires_at,
                    ).where(discovery_analysis_sessions.c.id == session_id)
                )
            ).one_or_none()
            if session is None:
                return
            retention_duration = session.expires_at - session.created_at
            retained_until = now + retention_duration
            cache_reusable_until = min(now + timedelta(minutes=15), retained_until)
            completed = (
                await connection.execute(
                update(discovery_analysis_sessions)
                .where(
                    discovery_analysis_sessions.c.id == session_id,
                    discovery_analysis_sessions.c.status == AnalysisSessionStatus.RUNNING.value,
                    discovery_analysis_sessions.c.job_stage == "analyzing",
                    discovery_analysis_sessions.c.cancellation_requested.is_(False),
                )
                .values(
                    status=AnalysisSessionStatus.COMPLETED.value,
                    job_stage="completed",
                    cache_reusable_until=cache_reusable_until,
                    expires_at=retained_until,
                    updated_at=now,
                    completed_at=now,
                )
                .returning(discovery_analysis_sessions.c.id)
                )
            ).scalar_one_or_none()
            if completed is not None:
                session_candidate_ids = select(candidate_analyses.c.id).where(
                    candidate_analyses.c.session_id == session_id
                )
                await connection.execute(
                    update(candidate_analyses)
                    .where(candidate_analyses.c.session_id == session_id)
                    .values(expires_at=retained_until)
                )
                await connection.execute(
                    update(artifact_references)
                    .where(
                        artifact_references.c.candidate_analysis_id.in_(
                            session_candidate_ids
                        ),
                        artifact_references.c.lifecycle
                        == ArtifactLifecycle.TEMPORARY.value,
                        artifact_references.c.removed_at.is_(None),
                    )
                    .values(expires_at=retained_until)
                )
                await connection.execute(
                    insert(discovery_job_events).values(
                        id=uuid4(),
                        workspace_id=session.workspace_id,
                        session_id=session_id,
                        actor_kind="worker",
                        actor_user_id=None,
                        event_type="job_completed",
                        safe_metadata={},
                        created_at=now,
                    )
                )
