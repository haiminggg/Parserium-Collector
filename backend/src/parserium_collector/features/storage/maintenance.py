from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from parserium_collector.features.storage.errors import (
    ArtifactConfigurationError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStorageError,
    ArtifactStorageUnavailableError,
    InvalidStorageKeyError,
)
from parserium_collector.features.storage.models import StoredObjectMetadata
from parserium_collector.features.storage.protocols import ArtifactStore
from parserium_collector.features.storage.repository import (
    ArtifactObjectRecord,
    ArtifactReferenceRecord,
    WorkspaceStorageUsageRecord,
)

Clock = Callable[[], datetime]
SAFE_STORAGE_FAILURE_CODES = {
    ArtifactStorageError.code,
    ArtifactStorageUnavailableError.code,
    ArtifactIntegrityError.code,
    ArtifactConfigurationError.code,
    ArtifactNotFoundError.code,
    InvalidStorageKeyError.code,
}


class MaintenanceRepository(Protocol):
    async def claim_expired_references(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[ArtifactReferenceRecord, ...]: ...

    async def claim_orphan_objects(
        self,
        *,
        now: datetime,
        orphaned_before: datetime,
        limit: int,
    ) -> tuple[ArtifactObjectRecord, ...]: ...

    async def claim_deletions(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ArtifactObjectRecord, ...]: ...

    async def complete_deletion(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        *,
        now: datetime,
    ) -> bool: ...

    async def fail_deletion(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> bool: ...

    async def claim_legacy_pending(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ArtifactObjectRecord, ...]: ...

    async def complete_legacy_metadata(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> bool: ...

    async def fail_legacy_metadata(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> bool: ...

    async def list_usage_reconciliation_candidates(self, *, limit: int) -> tuple[UUID, ...]: ...

    async def reconcile_usage(
        self,
        workspace_id: UUID,
        *,
        now: datetime,
    ) -> WorkspaceStorageUsageRecord: ...


class ScratchCleaner(Protocol):
    async def cleanup_stale(self, now: datetime, *, stale_seconds: int) -> int: ...


class ArtifactMaintenanceService:
    def __init__(
        self,
        *,
        repository: MaintenanceRepository,
        artifact_store: ArtifactStore,
        scratch_storage: ScratchCleaner,
        worker_id: str,
        orphan_grace_seconds: int,
        scratch_stale_seconds: int,
        lease_seconds: int = 60,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 3600,
        batch_limit: int = 100,
        reconciliation_limit: int = 10,
        clock: Clock | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("The maintenance worker identifier must not be empty.")
        if orphan_grace_seconds < 1 or scratch_stale_seconds < 1:
            raise ValueError("Maintenance retention intervals must be positive.")
        if lease_seconds < 1:
            raise ValueError("The maintenance lease interval must be positive.")
        if retry_base_seconds < 1 or retry_max_seconds < retry_base_seconds:
            raise ValueError("Maintenance retry intervals are invalid.")
        if batch_limit < 1 or reconciliation_limit < 1:
            raise ValueError("Maintenance batch limits must be positive.")
        self._repository = repository
        self._artifact_store = artifact_store
        self._scratch_storage = scratch_storage
        self._worker_id = worker_id
        self._orphan_grace_seconds = orphan_grace_seconds
        self._scratch_stale_seconds = scratch_stale_seconds
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._batch_limit = batch_limit
        self._reconciliation_limit = reconciliation_limit
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run_once(self) -> bool:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Artifact maintenance requires a timezone-aware clock.")
        handled = 0
        expired = await self._repository.claim_expired_references(
            now=now,
            limit=self._batch_limit,
        )
        handled += len(expired)
        orphans = await self._repository.claim_orphan_objects(
            now=now,
            orphaned_before=now - timedelta(seconds=self._orphan_grace_seconds),
            limit=self._batch_limit,
        )
        handled += len(orphans)

        lease_expires_at = now + timedelta(seconds=self._lease_seconds)
        deletions = await self._repository.claim_deletions(
            self._worker_id,
            now=now,
            lease_expires_at=lease_expires_at,
            limit=self._batch_limit,
        )
        for artifact in deletions:
            await self._delete_artifact(artifact, now)
        handled += len(deletions)

        legacy_objects = await self._repository.claim_legacy_pending(
            self._worker_id,
            now=now,
            lease_expires_at=lease_expires_at,
            limit=self._batch_limit,
        )
        for artifact in legacy_objects:
            await self._resolve_legacy_metadata(artifact, now)
        handled += len(legacy_objects)

        handled += await self._scratch_storage.cleanup_stale(
            now,
            stale_seconds=self._scratch_stale_seconds,
        )
        workspaces = await self._repository.list_usage_reconciliation_candidates(
            limit=self._reconciliation_limit
        )
        for workspace_id in workspaces:
            await self._repository.reconcile_usage(workspace_id, now=now)
        handled += len(workspaces)
        return handled > 0

    async def _delete_artifact(self, artifact: ArtifactObjectRecord, now: datetime) -> None:
        try:
            await self._artifact_store.delete(artifact.storage_key)
        except ArtifactNotFoundError:
            pass
        except Exception as error:
            await self._repository.fail_deletion(
                artifact.id,
                artifact.workspace_id,
                self._worker_id,
                error_code=self._failure_code(error),
                retry_at=self._retry_at(artifact.delete_attempt_count, error, now),
                now=now,
            )
            return
        await self._repository.complete_deletion(
            artifact.id,
            artifact.workspace_id,
            self._worker_id,
            now=now,
        )

    async def _resolve_legacy_metadata(
        self,
        artifact: ArtifactObjectRecord,
        now: datetime,
    ) -> None:
        try:
            metadata = await self._artifact_store.stat(artifact.storage_key)
        except Exception as error:
            await self._repository.fail_legacy_metadata(
                artifact.id,
                artifact.workspace_id,
                self._worker_id,
                error_code=self._failure_code(error),
                retry_at=self._retry_at(artifact.delete_attempt_count, error, now),
                now=now,
            )
            return
        await self._repository.complete_legacy_metadata(
            artifact.id,
            artifact.workspace_id,
            self._worker_id,
            metadata,
            now=now,
        )

    def _retry_at(self, attempt_count: int, error: Exception, now: datetime) -> datetime:
        retryable = isinstance(error, ArtifactStorageError) and error.retryable
        if retryable:
            exponent = min(attempt_count, 30)
            delay = min(self._retry_base_seconds * (2**exponent), self._retry_max_seconds)
        else:
            delay = self._retry_max_seconds
        return now + timedelta(seconds=delay)

    @staticmethod
    def _failure_code(error: Exception) -> str:
        if isinstance(error, ArtifactStorageError) and error.code in SAFE_STORAGE_FAILURE_CODES:
            return error.code
        return ArtifactStorageError.code
