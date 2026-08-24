import asyncio
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from parserium_collector.adapters.database.tables import worker_heartbeats
from parserium_collector.features.health.models import ComponentHealth, ComponentStatus
from parserium_collector.features.health.service import Probe


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


def storage_probe(root: Path, reserve_bytes: int, reserve_ratio: float) -> Probe:
    def check() -> ComponentHealth:
        probe_path: Path | None = None
        payload = b""
        try:
            root.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(root)
            reserve = max(reserve_bytes, int(usage.total * reserve_ratio))
            if usage.free <= reserve:
                return ComponentHealth(
                    name="storage",
                    status=ComponentStatus.UNAVAILABLE,
                    detail="Free storage is below the configured reserve.",
                )
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as handle:
                probe_path = Path(handle.name)
                payload = os.urandom(32)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if probe_path.read_bytes() != payload:
                raise OSError("Storage probe readback mismatch.")
            probe_path.unlink()
            probe_path = None
        except OSError:
            return ComponentHealth(
                name="storage",
                status=ComponentStatus.UNAVAILABLE,
                detail="Storage write, read, or delete probe failed.",
            )
        finally:
            if probe_path is not None:
                try:
                    probe_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return ComponentHealth(name="storage", status=ComponentStatus.AVAILABLE)

    async def probe() -> ComponentHealth:
        return await asyncio.to_thread(check)

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

