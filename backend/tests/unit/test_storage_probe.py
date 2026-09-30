from dataclasses import dataclass

from parserium_collector.features.health.models import ComponentStatus
from parserium_collector.features.health.probes import storage_probe
from parserium_collector.features.storage.errors import ArtifactStorageUnavailableError


@dataclass
class ArtifactStoreDouble:
    error: Exception | None = None
    probe_key: str | None = None

    async def probe(self, probe_key: str) -> None:
        self.probe_key = probe_key
        if self.error is not None:
            raise self.error


@dataclass
class LegacyRepositoryDouble:
    count: int = 0
    calls: int = 0

    async def count_live_legacy_pending(self) -> int:
        self.calls += 1
        return self.count


async def test_storage_probe_uses_configured_store_and_stable_instance_key() -> None:
    store = ArtifactStoreDouble()
    probe = storage_probe(store, "api-1", legacy_repository=None)

    result = await probe()

    assert result.status is ComponentStatus.AVAILABLE
    assert store.probe_key == "internal/health/api-1"


async def test_storage_probe_returns_only_safe_failure_detail() -> None:
    store = ArtifactStoreDouble(
        error=ArtifactStorageUnavailableError("credential and endpoint details")
    )
    probe = storage_probe(store, "api-1", legacy_repository=None)

    result = await probe()

    assert result.status is ComponentStatus.UNAVAILABLE
    assert result.detail == "Durable storage readiness probe failed."
    assert "credential" not in result.detail


async def test_s3_readiness_rejects_live_legacy_objects_before_provider_probe() -> None:
    store = ArtifactStoreDouble()
    repository = LegacyRepositoryDouble(count=2)
    probe = storage_probe(
        store,
        "api-1",
        legacy_repository=repository,
        reject_legacy_pending=True,
    )

    result = await probe()

    assert result.status is ComponentStatus.UNAVAILABLE
    assert result.detail == "Legacy artifact metadata reconciliation is incomplete."
    assert repository.calls == 1
    assert store.probe_key is None


async def test_filesystem_readiness_does_not_require_legacy_reconciliation() -> None:
    store = ArtifactStoreDouble()
    repository = LegacyRepositoryDouble(count=2)
    probe = storage_probe(
        store,
        "api-1",
        legacy_repository=repository,
        reject_legacy_pending=False,
    )

    result = await probe()

    assert result.status is ComponentStatus.AVAILABLE
    assert repository.calls == 0
    assert store.probe_key == "internal/health/api-1"
