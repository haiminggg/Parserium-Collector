from pathlib import Path
from uuid import UUID

import pytest

from parserium_collector.features.acquisition.errors import (
    ExportUnavailableError,
    StorageCapacityError,
    StorageContainmentError,
)
from parserium_collector.features.acquisition.export_storage import (
    LocalExportStorage,
    safe_windows_filename,
)
from parserium_collector.features.acquisition.models import DocumentType

WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")


def export_storage(root: Path | None) -> LocalExportStorage:
    return LocalExportStorage(
        export_root=root,
        disk_usage=lambda _: (10_000, 100, 9_900),
    )


async def test_export_copies_a_materialized_source_into_a_workspace(tmp_path: Path) -> None:
    root = tmp_path / "exports"
    root.mkdir()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"document")
    storage = export_storage(root)

    first = await storage.export_document(
        WORKSPACE_ID,
        source,
        "bank/2026",
        "annual report.pdf",
        DocumentType.PDF,
    )
    second = await storage.export_document(
        WORKSPACE_ID,
        source,
        "bank/2026",
        "annual report.pdf",
        DocumentType.PDF,
    )

    assert first.relative_path == "bank/2026/annual report.pdf"
    assert first.path.read_bytes() == b"document"
    assert second.relative_path == "bank/2026/annual report (1).pdf"


@pytest.mark.parametrize("relative_directory", ["../escape", "/absolute", "C:/escape"])
async def test_export_rejects_uncontained_directories(
    tmp_path: Path,
    relative_directory: str,
) -> None:
    root = tmp_path / "exports"
    root.mkdir()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"document")

    with pytest.raises(StorageContainmentError):
        await export_storage(root).export_document(
            WORKSPACE_ID,
            source,
            relative_directory,
            "report.pdf",
            DocumentType.PDF,
        )


async def test_export_rejects_a_linked_source(tmp_path: Path) -> None:
    root = tmp_path / "exports"
    root.mkdir()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"document")
    linked = tmp_path / "linked.pdf"
    try:
        linked.symlink_to(source)
    except OSError:
        pytest.skip("File symlinks are unavailable in this environment.")

    with pytest.raises(StorageContainmentError):
        await export_storage(root).export_document(
            WORKSPACE_ID,
            linked,
            "",
            "report.pdf",
            DocumentType.PDF,
        )


async def test_export_checks_available_capacity(tmp_path: Path) -> None:
    root = tmp_path / "exports"
    root.mkdir()
    source = tmp_path / "source.pdf"
    source.write_bytes(b"document")
    storage = LocalExportStorage(
        export_root=root,
        disk_usage=lambda _: (100, 99, 1),
    )

    with pytest.raises(StorageCapacityError):
        await storage.export_document(
            WORKSPACE_ID,
            source,
            "",
            "report.pdf",
            DocumentType.PDF,
        )


async def test_export_is_unavailable_without_a_root(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"document")

    with pytest.raises(ExportUnavailableError):
        await export_storage(None).export_document(
            WORKSPACE_ID,
            source,
            "",
            "report.pdf",
            DocumentType.PDF,
        )


def test_safe_windows_filename_removes_unsafe_names() -> None:
    assert safe_windows_filename("CON.pdf", DocumentType.PDF) == "_CON.pdf"
    assert safe_windows_filename("folder\\quarter:one.docx", DocumentType.DOCX) == (
        "folder_quarter_one.docx"
    )
