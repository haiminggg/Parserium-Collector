import os
import re
import shutil
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import anyio

from parserium_collector.features.acquisition.errors import (
    ExportUnavailableError,
    StorageCapacityError,
    StorageContainmentError,
    StorageError,
)
from parserium_collector.features.acquisition.models import DocumentType

WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
WINDOWS_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
MAX_WINDOWS_FILENAME_LENGTH = 200
DiskUsageProvider = Callable[[Path], tuple[int, int, int]]


def _disk_usage(path: Path) -> tuple[int, int, int]:
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


def safe_windows_filename(source: str, document_type: DocumentType) -> str:
    extension = f".{document_type.value}"
    normalized = unicodedata.normalize("NFC", source)
    sanitized = WINDOWS_INVALID_FILENAME.sub("_", normalized).strip().rstrip(" .")
    if sanitized.lower().endswith(extension):
        stem = sanitized[: -len(extension)]
    elif "." in sanitized:
        stem = sanitized.rsplit(".", maxsplit=1)[0]
    else:
        stem = sanitized
    stem = stem.strip().rstrip(" .")
    if not stem:
        stem = "document"
    if stem.split(".", maxsplit=1)[0].upper() in WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    max_stem_length = MAX_WINDOWS_FILENAME_LENGTH - len(extension)
    stem = stem[:max_stem_length].rstrip(" .") or "document"
    return f"{stem}{extension}"


@dataclass(frozen=True)
class ExportPlacement:
    relative_path: str
    path: Path


class LocalExportStorage:
    def __init__(
        self,
        *,
        export_root: Path | None,
        disk_usage: DiskUsageProvider = _disk_usage,
    ) -> None:
        self._export_root = export_root
        self._disk_usage = disk_usage

    async def export_document(
        self,
        workspace_id: UUID,
        source_path: Path,
        relative_directory: str,
        target_filename: str,
        document_type: DocumentType,
    ) -> ExportPlacement:
        return await anyio.to_thread.run_sync(
            self._export_sync,
            workspace_id,
            source_path,
            relative_directory,
            target_filename,
            document_type,
        )

    def _export_sync(
        self,
        workspace_id: UUID,
        source_path: Path,
        relative_directory: str,
        target_filename: str,
        document_type: DocumentType,
    ) -> ExportPlacement:
        if self._export_root is None:
            raise ExportUnavailableError("Document export is not configured.")
        try:
            export_root = self._export_root.resolve(strict=True)
            if not export_root.is_dir():
                raise ExportUnavailableError("The document export root is unavailable.")
            if not isinstance(workspace_id, UUID):
                raise StorageContainmentError("The workspace export identifier is invalid.")
            source = self._regular_source(source_path)
            workspaces_root = export_root / "workspaces"
            self._prepare_directory(workspaces_root)
            workspace_root = workspaces_root / str(workspace_id)
            self._prepare_directory(workspace_root)
            resolved_workspace_root = workspace_root.resolve(strict=True)
            if resolved_workspace_root.parent != workspaces_root.resolve(strict=True):
                raise StorageContainmentError("The workspace export root is not contained.")
            target_directory = self._create_contained_directories(
                resolved_workspace_root,
                self._relative_directory_parts(relative_directory),
            )
            self._require_capacity(export_root, source.stat().st_size)
            filename = safe_windows_filename(target_filename, document_type)
            target = self._copy_without_collision(source, target_directory, filename)
            return ExportPlacement(
                relative_path=target.relative_to(resolved_workspace_root).as_posix(),
                path=target,
            )
        except StorageError:
            raise
        except OSError as error:
            raise StorageError("Document export failed.") from error

    @staticmethod
    def _regular_source(source_path: Path) -> Path:
        if source_path.is_symlink():
            raise StorageContainmentError("The export source must be a regular file.")
        source = source_path.resolve(strict=True)
        if not source.is_file():
            raise StorageContainmentError("The export source must be a regular file.")
        return source

    @staticmethod
    def _prepare_directory(path: Path) -> None:
        if path.is_symlink():
            raise StorageContainmentError("The export path contains a link.")
        path.mkdir(mode=0o700, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise StorageContainmentError("The export path is not a directory.")
        path.chmod(0o700)

    @staticmethod
    def _relative_directory_parts(relative_directory: str) -> tuple[str, ...]:
        normalized = relative_directory.strip().replace("\\", "/")
        if normalized.startswith("/") or WINDOWS_DRIVE_PATTERN.match(normalized):
            raise StorageContainmentError("The export directory must be relative.")
        parts = tuple(part for part in normalized.split("/") if part not in {"", "."})
        if ".." in parts:
            raise StorageContainmentError("The export directory cannot traverse parents.")
        for part in parts:
            if (
                WINDOWS_INVALID_FILENAME.search(part)
                or part.rstrip(" .") != part
                or part.split(".", maxsplit=1)[0].upper() in WINDOWS_RESERVED_NAMES
                or len(part) > MAX_WINDOWS_FILENAME_LENGTH
            ):
                raise StorageContainmentError("The export directory contains an invalid name.")
        return parts

    @staticmethod
    def _create_contained_directories(root: Path, parts: tuple[str, ...]) -> Path:
        current = root
        for part in parts:
            candidate = current / part
            if candidate.is_symlink():
                raise StorageContainmentError("The export directory contains a link.")
            candidate.mkdir(mode=0o700, exist_ok=True)
            resolved = candidate.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as error:
                raise StorageContainmentError(
                    "The export directory escapes the configured root."
                ) from error
            current = resolved
        return current

    def _require_capacity(self, root: Path, size_bytes: int) -> None:
        _, _, free = self._disk_usage(root)
        if free - size_bytes <= 0:
            raise StorageCapacityError("Free export storage is insufficient.")

    @staticmethod
    def _copy_without_collision(source: Path, directory: Path, filename: str) -> Path:
        filename_path = Path(filename)
        for collision_index in range(10_000):
            suffix = "" if collision_index == 0 else f" ({collision_index})"
            candidate = directory / f"{filename_path.stem}{suffix}{filename_path.suffix}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(candidate, flags, 0o600)
            except FileExistsError:
                continue
            try:
                with (
                    source.open("rb") as source_handle,
                    os.fdopen(descriptor, "wb") as target_handle,
                ):
                    shutil.copyfileobj(source_handle, target_handle)
                    target_handle.flush()
                    os.fsync(target_handle.fileno())
                return candidate
            except Exception:
                candidate.unlink(missing_ok=True)
                raise
        raise StorageError("No collision-safe export filename is available.")
