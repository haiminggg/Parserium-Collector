import hashlib
import stat
from pathlib import Path

import pytest

from parserium_collector.features.storage.errors import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStorageUnavailableError,
    InvalidStorageKeyError,
)
from parserium_collector.features.storage.filesystem import FilesystemArtifactStore

from .contract import (
    DOCUMENT_KEY,
    PDF_CONTENT,
    assert_artifact_store_contract,
)


def filesystem_store(
    root: Path,
    *,
    disk_usage: tuple[int, int, int] = (10_000, 100, 9_900),
) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(
        root,
        reserve_bytes=100,
        reserve_ratio=0.01,
        disk_usage=lambda _: disk_usage,
    )


async def test_filesystem_adapter_contract(tmp_path: Path) -> None:
    await assert_artifact_store_contract(filesystem_store(tmp_path / "durable"), tmp_path)


async def test_filesystem_does_not_presign(tmp_path: Path) -> None:
    store = filesystem_store(tmp_path / "durable")

    assert (
        await store.presign_get(
            DOCUMENT_KEY,
            filename="annual report.pdf",
            media_type="application/pdf",
            ttl_seconds=60,
        )
        is None
    )


@pytest.mark.parametrize(
    ("sha256", "size_bytes"),
    [
        ("0" * 64, len(PDF_CONTENT)),
        (hashlib.sha256(PDF_CONTENT).hexdigest(), len(PDF_CONTENT) + 1),
    ],
)
async def test_filesystem_rejects_mismatched_upload_metadata(
    tmp_path: Path,
    sha256: str,
    size_bytes: int,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_CONTENT)

    with pytest.raises(ArtifactIntegrityError):
        await filesystem_store(tmp_path / "durable").put_file(
            DOCUMENT_KEY,
            source,
            media_type="application/pdf",
            sha256=sha256,
            size_bytes=size_bytes,
        )


async def test_filesystem_rejects_a_source_link(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_CONTENT)
    linked = tmp_path / "linked.pdf"
    try:
        linked.symlink_to(source)
    except OSError:
        pytest.skip("File symlinks are unavailable in this environment.")

    with pytest.raises(ArtifactIntegrityError):
        await filesystem_store(tmp_path / "durable").put_file(
            DOCUMENT_KEY,
            linked,
            media_type="application/pdf",
            sha256=hashlib.sha256(PDF_CONTENT).hexdigest(),
            size_bytes=len(PDF_CONTENT),
        )


async def test_filesystem_rejects_a_link_in_the_object_path(tmp_path: Path) -> None:
    root = tmp_path / "durable"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "workspaces").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable in this environment.")
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_CONTENT)

    with pytest.raises(InvalidStorageKeyError):
        await filesystem_store(root).put_file(
            DOCUMENT_KEY,
            source,
            media_type="application/pdf",
            sha256=hashlib.sha256(PDF_CONTENT).hexdigest(),
            size_bytes=len(PDF_CONTENT),
        )


async def test_filesystem_rejects_invalid_keys_before_io(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_CONTENT)

    with pytest.raises(InvalidStorageKeyError):
        await filesystem_store(tmp_path / "durable").put_file(
            "../outside",
            source,
            media_type="application/pdf",
            sha256=hashlib.sha256(PDF_CONTENT).hexdigest(),
            size_bytes=len(PDF_CONTENT),
        )


async def test_filesystem_enforces_reserve_before_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_CONTENT)

    with pytest.raises(ArtifactStorageUnavailableError, match="reserve"):
        await filesystem_store(
            tmp_path / "durable",
            disk_usage=(1_000, 900, 100),
        ).put_file(
            DOCUMENT_KEY,
            source,
            media_type="application/pdf",
            sha256=hashlib.sha256(PDF_CONTENT).hexdigest(),
            size_bytes=len(PDF_CONTENT),
        )


async def test_filesystem_upload_is_private_and_atomic(tmp_path: Path) -> None:
    root = tmp_path / "durable"
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_CONTENT)
    store = filesystem_store(root)

    await store.put_file(
        DOCUMENT_KEY,
        source,
        media_type="application/pdf",
        sha256=hashlib.sha256(PDF_CONTENT).hexdigest(),
        size_bytes=len(PDF_CONTENT),
    )

    target = root.joinpath(*DOCUMENT_KEY.split("/"))
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(target.parent.glob("*.part")) == []


async def test_filesystem_probe_cleans_its_object(tmp_path: Path) -> None:
    store = filesystem_store(tmp_path / "durable")
    key = "internal/health/api-1"

    await store.probe(key)

    with pytest.raises(ArtifactNotFoundError):
        await store.stat(key)
