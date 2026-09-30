import hashlib
from pathlib import Path
from uuid import UUID

import pytest

from parserium_collector.features.storage.errors import ArtifactNotFoundError
from parserium_collector.features.storage.keys import artifact_key
from parserium_collector.features.storage.models import ArtifactKind, ArtifactResourceKind
from parserium_collector.features.storage.protocols import ArtifactStore

WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000002")
DOCUMENT_KEY = artifact_key(
    WORKSPACE_ID,
    ArtifactResourceKind.DOCUMENT,
    DOCUMENT_ID,
    ArtifactKind.STORED_DOCUMENT,
)
PDF_CONTENT = b"%PDF-1.7\nverified artifact\n%%EOF\n"


async def assert_artifact_store_contract(store: ArtifactStore, tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_CONTENT)
    digest = hashlib.sha256(PDF_CONTENT).hexdigest()

    uploaded = await store.put_file(
        DOCUMENT_KEY,
        source,
        media_type="application/pdf",
        sha256=digest,
        size_bytes=len(PDF_CONTENT),
    )
    assert uploaded.storage_key == DOCUMENT_KEY
    assert uploaded.media_type == "application/pdf"
    assert uploaded.sha256 == digest
    assert uploaded.size_bytes == len(PDF_CONTENT)
    assert await store.stat(DOCUMENT_KEY) == uploaded

    chunks: list[bytes] = []
    async with store.open_reader(DOCUMENT_KEY) as reader:
        while chunk := await reader.read(7):
            chunks.append(chunk)
    assert b"".join(chunks) == PDF_CONTENT

    await store.delete(DOCUMENT_KEY)
    await store.delete(DOCUMENT_KEY)
    with pytest.raises(ArtifactNotFoundError):
        await store.stat(DOCUMENT_KEY)
