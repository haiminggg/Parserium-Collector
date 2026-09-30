from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from parserium_collector.features.storage.errors import (
    ArtifactConfigurationError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStorageUnavailableError,
)
from parserium_collector.features.storage.maintenance import ArtifactMaintenanceService
from parserium_collector.features.storage.models import (
    ArtifactObjectState,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import ArtifactObjectRecord

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")
OBJECT_ID = UUID("00000000-0000-0000-0000-000000000002")
LEGACY_ID = UUID("00000000-0000-0000-0000-000000000003")
KEY = f"workspaces/{WORKSPACE_ID}/document/{OBJECT_ID}/stored_document"
LEGACY_KEY = f"workspaces/{WORKSPACE_ID}/analysis/{LEGACY_ID}/source_pdf"


def object_record(
    *,
    object_id: UUID = OBJECT_ID,
    storage_key: str = KEY,
    state: ArtifactObjectState = ArtifactObjectState.DELETING,
    attempt_count: int = 0,
) -> ArtifactObjectRecord:
    legacy = state is ArtifactObjectState.LEGACY_PENDING
    return ArtifactObjectRecord(
        id=object_id,
        workspace_id=WORKSPACE_ID,
        storage_key=storage_key,
        media_type="application/pdf",
        size_bytes=None if legacy else 23,
        sha256=None if legacy else "a" * 64,
        state=state,
        available_at=None if legacy else NOW,
        delete_attempt_count=attempt_count,
        delete_available_at=NOW if state is ArtifactObjectState.DELETING else None,
        delete_claimed_by="maintenance-1",
        delete_lease_expires_at=NOW + timedelta(seconds=60),
        failure_code=None,
        created_at=NOW - timedelta(hours=2),
        updated_at=NOW,
        deleted_at=None,
    )


@dataclass
class RepositoryDouble:
    events: list[str] = field(default_factory=list)
    expired_count: int = 0
    orphan_count: int = 0
    deletion_claims: tuple[ArtifactObjectRecord, ...] = ()
    legacy_claims: tuple[ArtifactObjectRecord, ...] = ()
    usage_candidates: tuple[UUID, ...] = ()
    failure: tuple[str, datetime] | None = None
    legacy_failure: tuple[str, datetime] | None = None
    expired_args: tuple[datetime, int] | None = None
    orphan_args: tuple[datetime, datetime, int] | None = None
    deletion_args: tuple[str, datetime, datetime, int] | None = None
    legacy_args: tuple[str, datetime, datetime, int] | None = None
    completed: list[UUID] = field(default_factory=list)
    legacy_completed: list[StoredObjectMetadata] = field(default_factory=list)
    reconciled: list[UUID] = field(default_factory=list)

    async def claim_expired_references(self, *, now: datetime, limit: int) -> tuple[object, ...]:
        self.events.append("expire")
        self.expired_args = (now, limit)
        return tuple(object() for _ in range(self.expired_count))

    async def claim_orphan_objects(
        self,
        *,
        now: datetime,
        orphaned_before: datetime,
        limit: int,
    ) -> tuple[object, ...]:
        self.events.append("orphans")
        self.orphan_args = (now, orphaned_before, limit)
        return tuple(object() for _ in range(self.orphan_count))

    async def claim_deletions(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ArtifactObjectRecord, ...]:
        self.events.append("claim_deletions")
        self.deletion_args = (worker_id, now, lease_expires_at, limit)
        return self.deletion_claims

    async def complete_deletion(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        *,
        now: datetime,
    ) -> bool:
        assert workspace_id == WORKSPACE_ID
        assert worker_id == "maintenance-1"
        assert now == NOW
        self.events.append("complete_deletion")
        self.completed.append(artifact_object_id)
        return True

    async def fail_deletion(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> bool:
        assert artifact_object_id == OBJECT_ID
        assert workspace_id == WORKSPACE_ID
        assert worker_id == "maintenance-1"
        assert now == NOW
        self.failure = (error_code, retry_at)
        return True

    async def claim_legacy_pending(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ArtifactObjectRecord, ...]:
        self.events.append("claim_legacy")
        self.legacy_args = (worker_id, now, lease_expires_at, limit)
        return self.legacy_claims

    async def complete_legacy_metadata(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> bool:
        assert artifact_object_id == LEGACY_ID
        assert workspace_id == WORKSPACE_ID
        assert worker_id == "maintenance-1"
        assert now == NOW
        self.events.append("complete_legacy")
        self.legacy_completed.append(object_metadata)
        return True

    async def fail_legacy_metadata(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> bool:
        assert artifact_object_id == LEGACY_ID
        assert workspace_id == WORKSPACE_ID
        assert worker_id == "maintenance-1"
        assert now == NOW
        self.legacy_failure = (error_code, retry_at)
        return True

    async def list_usage_reconciliation_candidates(self, *, limit: int) -> tuple[UUID, ...]:
        self.events.append(f"usage_candidates:{limit}")
        return self.usage_candidates[:limit]

    async def reconcile_usage(self, workspace_id: UUID, *, now: datetime) -> object:
        assert now == NOW
        self.events.append("reconcile")
        self.reconciled.append(workspace_id)
        return object()


@dataclass
class StoreDouble:
    repository: RepositoryDouble
    delete_error: Exception | None = None
    stat_error: Exception | None = None
    deleted: list[str] = field(default_factory=list)
    metadata: StoredObjectMetadata = field(
        default_factory=lambda: StoredObjectMetadata(
            storage_key=LEGACY_KEY,
            media_type="application/pdf",
            size_bytes=29,
            sha256="b" * 64,
        )
    )

    async def delete(self, storage_key: str) -> None:
        self.repository.events.append("delete")
        self.deleted.append(storage_key)
        if self.delete_error is not None:
            raise self.delete_error

    async def stat(self, storage_key: str) -> StoredObjectMetadata:
        self.repository.events.append("stat")
        assert storage_key == LEGACY_KEY
        if self.stat_error is not None:
            raise self.stat_error
        return self.metadata


@dataclass
class ScratchDouble:
    repository: RepositoryDouble
    result: int = 0
    args: tuple[datetime, int] | None = None

    async def cleanup_stale(self, now: datetime, *, stale_seconds: int) -> int:
        self.repository.events.append("scratch")
        self.args = (now, stale_seconds)
        return self.result


def service(
    repository: RepositoryDouble,
    store: StoreDouble,
    scratch: ScratchDouble,
) -> ArtifactMaintenanceService:
    return ArtifactMaintenanceService(
        repository=repository,
        artifact_store=store,
        scratch_storage=scratch,
        worker_id="maintenance-1",
        orphan_grace_seconds=3600,
        scratch_stale_seconds=86400,
        lease_seconds=60,
        retry_base_seconds=30,
        retry_max_seconds=3600,
        batch_limit=7,
        reconciliation_limit=3,
        clock=lambda: NOW,
    )


async def test_run_once_executes_bounded_passes_in_dependency_order() -> None:
    deletion = object_record()
    legacy = object_record(
        object_id=LEGACY_ID,
        storage_key=LEGACY_KEY,
        state=ArtifactObjectState.LEGACY_PENDING,
    )
    repository = RepositoryDouble(
        expired_count=2,
        orphan_count=1,
        deletion_claims=(deletion,),
        legacy_claims=(legacy,),
        usage_candidates=(WORKSPACE_ID,),
    )
    store = StoreDouble(repository)
    scratch = ScratchDouble(repository, result=1)

    handled = await service(repository, store, scratch).run_once()

    assert handled
    assert repository.events == [
        "expire",
        "orphans",
        "claim_deletions",
        "delete",
        "complete_deletion",
        "claim_legacy",
        "stat",
        "complete_legacy",
        "scratch",
        "usage_candidates:3",
        "reconcile",
    ]
    assert repository.expired_args == (NOW, 7)
    assert repository.orphan_args == (NOW, NOW - timedelta(hours=1), 7)
    assert repository.deletion_args == (
        "maintenance-1",
        NOW,
        NOW + timedelta(seconds=60),
        7,
    )
    assert repository.legacy_args == repository.deletion_args
    assert repository.completed == [OBJECT_ID]
    assert repository.legacy_completed == [store.metadata]
    assert scratch.args == (NOW, 86400)
    assert repository.reconciled == [WORKSPACE_ID]


async def test_missing_physical_object_completes_idempotent_deletion() -> None:
    repository = RepositoryDouble(deletion_claims=(object_record(),))
    store = StoreDouble(repository, delete_error=ArtifactNotFoundError("already gone"))

    await service(repository, store, ScratchDouble(repository)).run_once()

    assert repository.completed == [OBJECT_ID]
    assert repository.failure is None


async def test_retryable_deletion_uses_capped_exponential_backoff() -> None:
    repository = RepositoryDouble(deletion_claims=(object_record(attempt_count=2),))
    store = StoreDouble(
        repository,
        delete_error=ArtifactStorageUnavailableError("provider detail must not persist"),
    )

    await service(repository, store, ScratchDouble(repository)).run_once()

    assert repository.completed == []
    assert repository.failure == ("storage_unavailable", NOW + timedelta(seconds=120))


async def test_terminal_deletion_failure_uses_safe_category_and_maximum_retry_delay() -> None:
    repository = RepositoryDouble(deletion_claims=(object_record(),))
    store = StoreDouble(
        repository,
        delete_error=ArtifactConfigurationError("secret provider response"),
    )

    await service(repository, store, ScratchDouble(repository)).run_once()

    assert repository.failure == (
        "storage_configuration_error",
        NOW + timedelta(seconds=3600),
    )


async def test_legacy_metadata_failure_releases_lease_without_scheduling_deletion() -> None:
    legacy = object_record(
        object_id=LEGACY_ID,
        storage_key=LEGACY_KEY,
        state=ArtifactObjectState.LEGACY_PENDING,
    )
    repository = RepositoryDouble(legacy_claims=(legacy,))
    store = StoreDouble(
        repository,
        stat_error=ArtifactIntegrityError("unsafe metadata detail"),
    )

    await service(repository, store, ScratchDouble(repository)).run_once()

    assert repository.legacy_completed == []
    assert repository.legacy_failure == (
        "storage_integrity_failure",
        NOW + timedelta(seconds=3600),
    )
