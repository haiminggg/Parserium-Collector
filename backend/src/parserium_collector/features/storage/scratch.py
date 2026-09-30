import os
import re
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

import anyio

from parserium_collector.features.storage.errors import (
    ScratchCapacityError,
    ScratchContainmentError,
    ScratchStorageError,
)
from parserium_collector.features.storage.protocols import ArtifactStore

SAFE_FILENAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
DiskUsageProvider = Callable[[Path], tuple[int, int, int]]


def _disk_usage(path: Path) -> tuple[int, int, int]:
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


class ScratchFile:
    def __init__(self, path: Path, handle: BinaryIO, max_bytes: int) -> None:
        self.path = path
        self._handle = handle
        self._max_bytes = max_bytes
        self._written = 0
        self._finished = False

    async def write(self, chunk: bytes) -> None:
        if self._finished:
            raise RuntimeError("The scratch file is already finished.")
        if self._written + len(chunk) > self._max_bytes:
            raise ScratchCapacityError("The scratch file exceeds its byte allowance.")
        await anyio.to_thread.run_sync(self._handle.write, chunk)
        self._written += len(chunk)

    async def finish(self) -> None:
        if not self._finished:
            await anyio.to_thread.run_sync(self._finish_sync)

    def _finish_sync(self) -> None:
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._handle.close()
        self._finished = True

    async def aclose(self) -> None:
        if not self._finished:
            await anyio.to_thread.run_sync(self._close_sync)

    def _close_sync(self) -> None:
        self._handle.close()
        self._finished = True


class ScratchJob:
    def __init__(self, storage: "ScratchStorage", directory: Path) -> None:
        self._storage = storage
        self.directory = directory
        self._files: list[ScratchFile] = []

    async def create_file(self, filename: str, *, max_bytes: int) -> ScratchFile:
        if max_bytes < 1:
            raise ValueError("The scratch file byte allowance must be positive.")
        self._storage._require_safe_filename(filename)
        self._storage._require_capacity(max_bytes)
        staged = await anyio.to_thread.run_sync(
            self._storage._create_file,
            self.directory,
            filename,
            max_bytes,
        )
        self._files.append(staged)
        return staged

    def path(self, filename: str) -> Path:
        self._storage._require_safe_filename(filename)
        return self.directory / filename

    async def materialize(
        self,
        store: ArtifactStore,
        storage_key: str,
        filename: str,
        *,
        max_bytes: int,
    ) -> Path:
        staged = await self.create_file(filename, max_bytes=max_bytes)
        async with store.open_reader(storage_key) as reader:
            while chunk := await reader.read(64 * 1024):
                await staged.write(chunk)
        await staged.finish()
        return staged.path

    async def close(self) -> None:
        for staged in self._files:
            await staged.aclose()


class ScratchStorage:
    def __init__(
        self,
        root: Path,
        *,
        reserve_bytes: int,
        reserve_ratio: float,
        disk_usage: DiskUsageProvider = _disk_usage,
    ) -> None:
        if reserve_bytes < 0 or not 0 <= reserve_ratio < 1:
            raise ValueError("Scratch reserve settings are invalid.")
        self._root = root
        self._workspaces_root = root / "workspaces"
        self._reserve_bytes = reserve_bytes
        self._reserve_ratio = reserve_ratio
        self._disk_usage = disk_usage

    @asynccontextmanager
    async def job(self, workspace_id: UUID, job_id: UUID) -> AsyncIterator[ScratchJob]:
        directory = await anyio.to_thread.run_sync(
            self._create_job_directory,
            workspace_id,
            job_id,
        )
        job = ScratchJob(self, directory)
        try:
            yield job
        finally:
            await job.close()
            await anyio.to_thread.run_sync(self._remove_job_directory, directory)

    async def cleanup_stale(self, now: datetime, *, stale_seconds: int) -> int:
        if stale_seconds < 1:
            raise ValueError("The scratch stale interval must be positive.")
        cutoff = self.stale_before(now, timedelta(seconds=stale_seconds))
        return await anyio.to_thread.run_sync(self._cleanup_stale_sync, cutoff)

    @staticmethod
    def stale_before(now: datetime, age: timedelta) -> datetime:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Scratch cleanup requires a timezone-aware clock.")
        if age.total_seconds() <= 0:
            raise ValueError("Scratch cleanup age must be positive.")
        return now - age

    def _create_job_directory(self, workspace_id: UUID, job_id: UUID) -> Path:
        if not isinstance(workspace_id, UUID) or not isinstance(job_id, UUID):
            raise ScratchContainmentError("Scratch identifiers must be UUIDs.")
        self._prepare_directory(self._root, parents=True)
        self._prepare_directory(self._workspaces_root)
        workspace_root = self._workspaces_root / str(workspace_id)
        self._prepare_directory(workspace_root)
        if workspace_root.resolve(strict=True).parent != self._workspaces_root.resolve(strict=True):
            raise ScratchContainmentError("The workspace scratch root is not contained.")
        job_directory = workspace_root / str(job_id)
        if job_directory.is_symlink() or job_directory.exists():
            raise ScratchContainmentError("The scratch job directory is unavailable.")
        try:
            job_directory.mkdir(mode=0o700)
            job_directory.chmod(0o700)
        except OSError as error:
            raise ScratchStorageError("Scratch job creation failed.") from error
        return job_directory

    @staticmethod
    def _create_file(directory: Path, filename: str, max_bytes: int) -> ScratchFile:
        path = directory / filename
        try:
            handle = path.open("xb")
            path.chmod(0o600)
        except OSError as error:
            raise ScratchStorageError("Scratch file creation failed.") from error
        return ScratchFile(path, handle, max_bytes)

    def _require_capacity(self, size_bytes: int) -> None:
        total, _, free = self._disk_usage(self._root)
        reserve = max(self._reserve_bytes, int(total * self._reserve_ratio))
        if free - size_bytes <= reserve:
            raise ScratchCapacityError("Free scratch storage is below the configured reserve.")

    @staticmethod
    def _require_safe_filename(filename: str) -> None:
        if SAFE_FILENAME_PATTERN.fullmatch(filename) is None or filename in {".", ".."}:
            raise ScratchContainmentError("The scratch filename is invalid.")

    @staticmethod
    def _prepare_directory(path: Path, *, parents: bool = False) -> None:
        if path.is_symlink():
            raise ScratchContainmentError("The scratch path contains a link.")
        try:
            path.mkdir(mode=0o700, parents=parents, exist_ok=True)
            if path.is_symlink() or not path.is_dir():
                raise ScratchContainmentError("The scratch path is not a directory.")
            path.chmod(0o700)
        except ScratchStorageError:
            raise
        except OSError as error:
            raise ScratchStorageError("Scratch directory preparation failed.") from error

    def _remove_job_directory(self, directory: Path) -> None:
        self._require_contained_job_directory(directory)
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            return
        except OSError as error:
            raise ScratchStorageError("Scratch job cleanup failed.") from error

    def _cleanup_stale_sync(self, cutoff: datetime) -> int:
        if not self._workspaces_root.exists():
            return 0
        if self._workspaces_root.is_symlink():
            raise ScratchContainmentError("The scratch path contains a link.")
        removed = 0
        for workspace_root in self._workspaces_root.iterdir():
            if workspace_root.is_symlink() or not workspace_root.is_dir():
                continue
            try:
                UUID(workspace_root.name)
            except ValueError:
                continue
            for job_directory in workspace_root.iterdir():
                if job_directory.is_symlink() or not job_directory.is_dir():
                    continue
                try:
                    UUID(job_directory.name)
                except ValueError:
                    continue
                modified = datetime.fromtimestamp(job_directory.stat().st_mtime, cutoff.tzinfo)
                if modified < cutoff:
                    self._remove_job_directory(job_directory)
                    removed += 1
        return removed

    def _require_contained_job_directory(self, directory: Path) -> None:
        if directory.is_symlink():
            raise ScratchContainmentError("The scratch job path contains a link.")
        try:
            UUID(directory.name)
            UUID(directory.parent.name)
            resolved_parent = directory.parent.resolve(strict=True)
            resolved_parent.relative_to(self._workspaces_root.resolve(strict=True))
        except (OSError, ValueError) as error:
            raise ScratchContainmentError("The scratch job path is not contained.") from error
