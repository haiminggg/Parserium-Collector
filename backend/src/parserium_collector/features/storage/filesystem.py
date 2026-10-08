import hashlib
import os
import secrets
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import anyio

from parserium_collector.features.storage.errors import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStorageError,
    ArtifactStorageUnavailableError,
    InvalidStorageKeyError,
)
from parserium_collector.features.storage.keys import validate_storage_key
from parserium_collector.features.storage.models import StoredObjectMetadata

DiskUsageProvider = Callable[[Path], tuple[int, int, int]]


def _disk_usage(path: Path) -> tuple[int, int, int]:
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


class _FilesystemReader:
    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle

    async def read(self, size: int = -1) -> bytes:
        return await anyio.to_thread.run_sync(self._handle.read, size)

    async def close(self) -> None:
        await anyio.to_thread.run_sync(self._handle.close)


class FilesystemArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        reserve_bytes: int,
        reserve_ratio: float,
        disk_usage: DiskUsageProvider = _disk_usage,
    ) -> None:
        if reserve_bytes < 0 or not 0 <= reserve_ratio < 1:
            raise ValueError("Filesystem artifact reserve settings are invalid.")
        self._root = root
        self._reserve_bytes = reserve_bytes
        self._reserve_ratio = reserve_ratio
        self._disk_usage = disk_usage

    async def put_file(
        self,
        storage_key: str,
        source: Path,
        *,
        media_type: str,
        sha256: str,
        size_bytes: int,
    ) -> StoredObjectMetadata:
        operation = partial(
            self._put_file_sync,
            storage_key,
            source,
            media_type=media_type,
            sha256=sha256,
            size_bytes=size_bytes,
        )
        return await anyio.to_thread.run_sync(operation)

    async def stat(self, storage_key: str) -> StoredObjectMetadata:
        return await anyio.to_thread.run_sync(self._stat_sync, storage_key)

    @asynccontextmanager
    async def open_reader(self, storage_key: str) -> AsyncIterator[_FilesystemReader]:
        handle = await anyio.to_thread.run_sync(self._open_reader_sync, storage_key)
        reader = _FilesystemReader(handle)
        try:
            yield reader
        finally:
            await reader.close()

    async def presign_get(
        self,
        storage_key: str,
        *,
        filename: str,
        media_type: str,
        ttl_seconds: int,
    ) -> None:
        validate_storage_key(storage_key)
        return None

    async def delete(self, storage_key: str) -> None:
        await anyio.to_thread.run_sync(self._delete_sync, storage_key)

    async def probe(self, probe_key: str) -> None:
        validate_storage_key(probe_key)
        await self.delete(probe_key)
        await anyio.to_thread.run_sync(self._prepare_root)
        source = self._root / f".probe-{uuid4().hex}.part"
        content = secrets.token_bytes(32)
        digest = hashlib.sha256(content).hexdigest()
        try:
            await anyio.to_thread.run_sync(self._write_probe_source, source, content)
            metadata = await self.put_file(
                probe_key,
                source,
                media_type="application/octet-stream",
                sha256=digest,
                size_bytes=len(content),
            )
            if metadata.sha256 != digest or metadata.size_bytes != len(content):
                raise ArtifactIntegrityError("The storage readiness probe metadata is invalid.")
            chunks: list[bytes] = []
            async with self.open_reader(probe_key) as reader:
                while chunk := await reader.read(64 * 1024):
                    chunks.append(chunk)
            if b"".join(chunks) != content:
                raise ArtifactIntegrityError("The storage readiness probe content is invalid.")
        finally:
            await anyio.to_thread.run_sync(source.unlink, True)
            await self.delete(probe_key)

    def _put_file_sync(
        self,
        storage_key: str,
        source: Path,
        *,
        media_type: str,
        sha256: str,
        size_bytes: int,
    ) -> StoredObjectMetadata:
        validate_storage_key(storage_key)
        try:
            expected = StoredObjectMetadata(
                storage_key=storage_key,
                media_type=media_type,
                size_bytes=size_bytes,
                sha256=sha256,
            )
        except ValueError as error:
            raise ArtifactIntegrityError("The expected artifact metadata is invalid.") from error
        if source.is_symlink():
            raise ArtifactIntegrityError("The artifact upload source is not a regular file.")
        try:
            resolved_source = source.resolve(strict=True)
        except OSError as error:
            raise ArtifactIntegrityError("The artifact upload source is unavailable.") from error
        if not resolved_source.is_file():
            raise ArtifactIntegrityError("The artifact upload source is not a regular file.")

        temporary: Path | None = None
        try:
            self._prepare_root()
            self._require_capacity(size_bytes)
            target = self._prepare_target(storage_key)
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.part")
            actual_sha256, actual_size = self._copy_and_hash(resolved_source, temporary)
            if actual_sha256 != expected.sha256 or actual_size != expected.size_bytes:
                raise ArtifactIntegrityError(
                    "The artifact upload metadata does not match its bytes."
                )
            detected_media_type = self._detect_media_type(temporary, storage_key)
            if detected_media_type != media_type:
                raise ArtifactIntegrityError(
                    "The artifact upload media type does not match its bytes."
                )
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise InvalidStorageKeyError("The artifact storage target is invalid.")
            os.replace(temporary, target)
            temporary = None
            target.chmod(0o600)
            actual = self._stat_sync(storage_key)
            if actual != expected:
                raise ArtifactIntegrityError("The stored artifact metadata is invalid.")
            return actual
        except ArtifactStorageError:
            raise
        except OSError as error:
            raise ArtifactStorageUnavailableError("Filesystem artifact upload failed.") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _stat_sync(self, storage_key: str) -> StoredObjectMetadata:
        path = self._resolve_existing_file(storage_key)
        try:
            sha256, size_bytes = self._hash_file(path)
            media_type = self._detect_media_type(path, storage_key)
            return StoredObjectMetadata(
                storage_key=storage_key,
                media_type=media_type,
                size_bytes=size_bytes,
                sha256=sha256,
            )
        except ArtifactStorageError:
            raise
        except OSError as error:
            raise ArtifactStorageUnavailableError(
                "Filesystem artifact metadata read failed."
            ) from error

    def _open_reader_sync(self, storage_key: str) -> BinaryIO:
        path = self._resolve_existing_file(storage_key)
        try:
            return path.open("rb")
        except OSError as error:
            raise ArtifactStorageUnavailableError("Filesystem artifact read failed.") from error

    def _delete_sync(self, storage_key: str) -> None:
        validate_storage_key(storage_key)
        if not self._root.exists():
            return
        try:
            target = self._candidate_path(storage_key, require_parents=False)
            if target is None or not target.exists():
                return
            if target.is_symlink() or not target.is_file():
                raise InvalidStorageKeyError("The artifact storage target is invalid.")
            target.unlink()
            current = target.parent
            root = self._root.resolve(strict=True)
            while current != root:
                try:
                    current.rmdir()
                except OSError:
                    break
                current = current.parent
        except ArtifactStorageError:
            raise
        except OSError as error:
            raise ArtifactStorageUnavailableError("Filesystem artifact deletion failed.") from error

    def _prepare_root(self) -> None:
        if self._root.is_symlink():
            raise InvalidStorageKeyError("The artifact storage root contains a link.")
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._root.is_symlink() or not self._root.is_dir():
                raise InvalidStorageKeyError("The artifact storage root is invalid.")
            self._root.chmod(0o700)
        except ArtifactStorageError:
            raise
        except OSError as error:
            raise ArtifactStorageUnavailableError(
                "Filesystem artifact root is unavailable."
            ) from error

    def _prepare_target(self, storage_key: str) -> Path:
        validate_storage_key(storage_key)
        root = self._root.resolve(strict=True)
        current = root
        segments = storage_key.split("/")
        for segment in segments[:-1]:
            candidate = current / segment
            if candidate.is_symlink():
                raise InvalidStorageKeyError("The artifact storage path contains a link.")
            candidate.mkdir(mode=0o700, exist_ok=True)
            if candidate.is_symlink() or not candidate.is_dir():
                raise InvalidStorageKeyError("The artifact storage path is invalid.")
            candidate.chmod(0o700)
            resolved = candidate.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as error:
                raise InvalidStorageKeyError(
                    "The artifact storage path is not contained."
                ) from error
            current = resolved
        return current / segments[-1]

    def _resolve_existing_file(self, storage_key: str) -> Path:
        validate_storage_key(storage_key)
        if not self._root.exists():
            raise ArtifactNotFoundError("The artifact does not exist.")
        target = self._candidate_path(storage_key, require_parents=True)
        if target is None or not target.exists():
            raise ArtifactNotFoundError("The artifact does not exist.")
        if target.is_symlink() or not target.is_file():
            raise InvalidStorageKeyError("The artifact storage target is invalid.")
        return target

    def _candidate_path(self, storage_key: str, *, require_parents: bool) -> Path | None:
        root = self._root.resolve(strict=True)
        current = root
        segments = storage_key.split("/")
        for segment in segments[:-1]:
            candidate = current / segment
            if candidate.is_symlink():
                raise InvalidStorageKeyError("The artifact storage path contains a link.")
            if not candidate.exists():
                if require_parents:
                    raise ArtifactNotFoundError("The artifact does not exist.")
                return None
            if not candidate.is_dir():
                raise InvalidStorageKeyError("The artifact storage path is invalid.")
            resolved = candidate.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as error:
                raise InvalidStorageKeyError(
                    "The artifact storage path is not contained."
                ) from error
            current = resolved
        return current / segments[-1]

    def _require_capacity(self, size_bytes: int) -> None:
        total, _, free = self._disk_usage(self._root)
        reserve = max(self._reserve_bytes, int(total * self._reserve_ratio))
        if free - size_bytes <= reserve:
            raise ArtifactStorageUnavailableError(
                "Free artifact storage is below the configured reserve."
            )

    @staticmethod
    def _copy_and_hash(source: Path, target: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size_bytes = 0
        with source.open("rb") as source_handle, target.open("xb") as target_handle:
            target.chmod(0o600)
            while chunk := source_handle.read(1024 * 1024):
                target_handle.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        return digest.hexdigest(), size_bytes

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size_bytes = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size_bytes += len(chunk)
        return digest.hexdigest(), size_bytes

    @staticmethod
    def _detect_media_type(path: Path, storage_key: str) -> str:
        with path.open("rb") as handle:
            header = handle.read(8)
        if header.startswith(b"%PDF-"):
            return "application/pdf"
        if header == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if header.startswith(b"PK\x03\x04"):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        suffix = Path(storage_key).suffix.lower()
        if suffix == ".pdf":
            return "application/pdf"
        if suffix == ".docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if suffix == ".png":
            return "image/png"
        return "application/octet-stream"

    @staticmethod
    def _write_probe_source(path: Path, content: bytes) -> None:
        with path.open("xb") as handle:
            path.chmod(0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
