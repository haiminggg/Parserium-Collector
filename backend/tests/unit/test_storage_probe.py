import asyncio
from pathlib import Path

from parserium_collector.features.health.models import ComponentStatus
from parserium_collector.features.health.probes import storage_probe


async def test_storage_probe_round_trips_bytes_and_removes_probe_file(tmp_path: Path) -> None:
    probe = storage_probe(tmp_path, reserve_bytes=0, reserve_ratio=0)

    result = await probe()

    assert result.status is ComponentStatus.AVAILABLE
    contents = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
    assert contents == []


async def test_storage_probe_rejects_free_space_below_reserve(tmp_path: Path) -> None:
    probe = storage_probe(tmp_path, reserve_bytes=2**63, reserve_ratio=0)

    result = await probe()

    assert result.status is ComponentStatus.UNAVAILABLE
    assert result.detail == "Free storage is below the configured reserve."
