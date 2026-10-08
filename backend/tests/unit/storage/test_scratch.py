import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from parserium_collector.features.storage.errors import (
    ScratchCapacityError,
    ScratchContainmentError,
)
from parserium_collector.features.storage.scratch import ScratchStorage

WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")
JOB_ID = UUID("00000000-0000-0000-0000-000000000002")


class MemoryReader:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._content) - self._offset
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class MemoryArtifactStore:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self.opened_keys: list[str] = []

    @asynccontextmanager
    async def open_reader(self, storage_key: str) -> AsyncIterator[MemoryReader]:
        self.opened_keys.append(storage_key)
        yield MemoryReader(self._content)


def scratch_for(
    root: Path,
    *,
    disk_usage: tuple[int, int, int] = (10_000, 100, 9_900),
) -> ScratchStorage:
    return ScratchStorage(
        root,
        reserve_bytes=100,
        reserve_ratio=0.01,
        disk_usage=lambda _: disk_usage,
    )


async def test_job_creates_restrictive_files_and_cleans_up(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    scratch = scratch_for(root)

    async with scratch.job(WORKSPACE_ID, JOB_ID) as job:
        staged = await job.create_file("source.pdf", max_bytes=100)
        await staged.write(b"document")
        await staged.finish()

        assert staged.path.read_bytes() == b"document"
        assert stat.S_IMODE(staged.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(staged.path.parent.stat().st_mode) == 0o700
        job_directory = staged.path.parent

    assert not job_directory.exists()


async def test_job_cleans_up_after_an_exception(tmp_path: Path) -> None:
    scratch = scratch_for(tmp_path / "scratch")
    job_directory: Path | None = None

    with pytest.raises(RuntimeError, match="stop"):
        async with scratch.job(WORKSPACE_ID, JOB_ID) as job:
            job_directory = job.directory
            raise RuntimeError("stop")

    assert job_directory is not None
    assert not job_directory.exists()


async def test_materialize_streams_to_a_contained_job_path(tmp_path: Path) -> None:
    store = MemoryArtifactStore(b"document bytes")
    scratch = scratch_for(tmp_path / "scratch")

    async with scratch.job(WORKSPACE_ID, JOB_ID) as job:
        path = await job.materialize(store, "workspaces/object", "source.pdf", max_bytes=100)

        assert path.read_bytes() == b"document bytes"
        assert path.is_relative_to((tmp_path / "scratch").resolve())
        assert store.opened_keys == ["workspaces/object"]


async def test_scratch_file_rejects_more_than_its_byte_limit(tmp_path: Path) -> None:
    scratch = scratch_for(tmp_path / "scratch")

    async with scratch.job(WORKSPACE_ID, JOB_ID) as job:
        staged = await job.create_file("source.pdf", max_bytes=4)
        with pytest.raises(ScratchCapacityError, match="allowance"):
            await staged.write(b"12345")


async def test_scratch_checks_the_configured_disk_reserve(tmp_path: Path) -> None:
    scratch = scratch_for(tmp_path / "scratch", disk_usage=(1_000, 900, 100))

    async with scratch.job(WORKSPACE_ID, JOB_ID) as job:
        with pytest.raises(ScratchCapacityError, match="reserve"):
            await job.create_file("source.pdf", max_bytes=1)


async def test_job_rejects_an_unsafe_filename(tmp_path: Path) -> None:
    scratch = scratch_for(tmp_path / "scratch")

    async with scratch.job(WORKSPACE_ID, JOB_ID) as job:
        for filename in ("../source.pdf", "folder/source.pdf", "source\\file.pdf", "."):
            with pytest.raises(ScratchContainmentError):
                await job.create_file(filename, max_bytes=100)


async def test_job_rejects_a_link_in_the_scratch_root(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()
    try:
        (root / "workspaces").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable in this environment.")

    scratch = scratch_for(root)
    with pytest.raises(ScratchContainmentError):
        async with scratch.job(WORKSPACE_ID, JOB_ID):
            pytest.fail("A linked scratch root must not be entered.")


async def test_cleanup_stale_removes_only_old_uuid_job_directories(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    workspace_root = root / "workspaces" / str(WORKSPACE_ID)
    stale = workspace_root / str(JOB_ID)
    recent = workspace_root / "00000000-0000-0000-0000-000000000003"
    ignored = workspace_root / "not-a-job"
    for path in (stale, recent, ignored):
        path.mkdir(parents=True, exist_ok=True)
        (path / "source.pdf").write_bytes(b"content")
    now = datetime(2026, 8, 31, tzinfo=UTC)
    os.utime(stale, (now.timestamp() - 7200, now.timestamp() - 7200))
    os.utime(recent, (now.timestamp() - 30, now.timestamp() - 30))
    os.utime(ignored, (now.timestamp() - 7200, now.timestamp() - 7200))

    removed = await scratch_for(root).cleanup_stale(now, stale_seconds=3600)

    assert removed == 1
    assert not stale.exists()
    assert recent.exists()
    assert ignored.exists()


def test_cleanup_stale_requires_an_aware_clock(tmp_path: Path) -> None:
    scratch = scratch_for(tmp_path / "scratch")

    with pytest.raises(ValueError, match="timezone-aware"):
        scratch.stale_before(datetime(2026, 8, 31), timedelta(hours=1))
