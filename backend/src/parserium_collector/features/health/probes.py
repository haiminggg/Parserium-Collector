from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from parserium_collector.adapters.database.tables import worker_heartbeats
from parserium_collector.features.health.models import ComponentHealth, ComponentStatus
from parserium_collector.features.health.service import Probe
from parserium_collector.features.storage.keys import health_probe_key
from parserium_collector.features.storage.protocols import ArtifactStore


class LegacyPendingRepository(Protocol):
    async def count_live_legacy_pending(self) -> int: ...


def database_probe(engine: AsyncEngine, expected_migration: str) -> Probe:
    async def probe() -> ComponentHealth:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        except Exception:
            return ComponentHealth(
                name="database",
                status=ComponentStatus.UNAVAILABLE,
                detail="Database transaction or migration check failed.",
            )
        if revision != expected_migration:
            return ComponentHealth(
                name="database",
                status=ComponentStatus.UNAVAILABLE,
                detail="Database migration does not match the application.",
            )
        return ComponentHealth(name="database", status=ComponentStatus.AVAILABLE)

    return probe


def storage_probe(
    artifact_store: ArtifactStore,
    instance_id: str,
    *,
    legacy_repository: LegacyPendingRepository | None,
    reject_legacy_pending: bool = False,
) -> Probe:
    probe_key = health_probe_key(instance_id)
    if reject_legacy_pending and legacy_repository is None:
        raise ValueError("S3 readiness requires an artifact registry.")
    required_legacy_repository = legacy_repository if reject_legacy_pending else None

    async def probe() -> ComponentHealth:
        if required_legacy_repository is not None:
            try:
                legacy_count = await required_legacy_repository.count_live_legacy_pending()
            except Exception:
                return ComponentHealth(
                    name="storage",
                    status=ComponentStatus.UNAVAILABLE,
                    detail="Storage registry readiness check failed.",
                )
            if legacy_count > 0:
                return ComponentHealth(
                    name="storage",
                    status=ComponentStatus.UNAVAILABLE,
                    detail="Legacy artifact metadata reconciliation is incomplete.",
                )
        try:
            await artifact_store.probe(probe_key)
        except Exception:
            return ComponentHealth(
                name="storage",
                status=ComponentStatus.UNAVAILABLE,
                detail="Durable storage readiness probe failed.",
            )
        return ComponentHealth(name="storage", status=ComponentStatus.AVAILABLE)

    return probe


def worker_probe(engine: AsyncEngine, stale_seconds: float) -> Probe:
    async def probe() -> ComponentHealth:
        try:
            async with engine.connect() as connection:
                last_seen: datetime | None = await connection.scalar(
                    select(func.max(worker_heartbeats.c.last_seen_at))
                )
        except Exception:
            return ComponentHealth(
                name="worker",
                status=ComponentStatus.UNAVAILABLE,
                detail="Worker heartbeat query failed.",
            )
        if last_seen is None:
            return ComponentHealth(
                name="worker",
                status=ComponentStatus.UNAVAILABLE,
                detail="No worker heartbeat exists.",
            )
        age = (datetime.now(UTC) - last_seen).total_seconds()
        if age > stale_seconds:
            return ComponentHealth(
                name="worker",
                status=ComponentStatus.UNAVAILABLE,
                detail="No fresh worker heartbeat.",
            )
        return ComponentHealth(name="worker", status=ComponentStatus.AVAILABLE)

    return probe
