from collections.abc import AsyncIterator

import pytest

from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactLifecycle,
    ArtifactObjectState,
    ArtifactResourceKind,
    ArtifactStream,
    StoredObjectMetadata,
)


def test_storage_enums_match_the_persisted_contract() -> None:
    assert {state.value for state in ArtifactObjectState} == {
        "legacy_pending",
        "uploading",
        "available",
        "deleting",
        "delete_failed",
        "deleted",
    }
    assert {lifecycle.value for lifecycle in ArtifactLifecycle} == {
        "temporary",
        "persistent",
    }
    assert {kind.value for kind in ArtifactResourceKind} == {
        "analysis",
        "document",
        "internal",
    }
    assert {kind.value for kind in ArtifactKind} == {
        "source_pdf",
        "source_docx",
        "converted_pdf",
        "preview_png",
        "stored_document",
    }


def test_stored_object_metadata_accepts_verified_values() -> None:
    metadata = StoredObjectMetadata(
        storage_key="workspaces/00000000-0000-0000-0000-000000000001/document/"
        "00000000-0000-0000-0000-000000000002/stored_document",
        media_type="application/pdf",
        size_bytes=128,
        sha256="a" * 64,
    )

    assert metadata.size_bytes == 128
    assert metadata.sha256 == "a" * 64


@pytest.mark.parametrize(
    "overrides",
    [
        {"size_bytes": -1},
        {"sha256": "not-a-digest"},
        {"sha256": "A" * 64},
        {"media_type": ""},
    ],
)
def test_stored_object_metadata_rejects_unverified_values(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "storage_key": "workspaces/a",
        "media_type": "application/pdf",
        "size_bytes": 128,
        "sha256": "a" * 64,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        StoredObjectMetadata(**values)  # type: ignore[arg-type]


async def _body() -> AsyncIterator[bytes]:
    yield b"body"


def test_artifact_stream_contains_only_safe_response_metadata() -> None:
    stream = ArtifactStream(
        body=_body(),
        media_type="image/png",
        size_bytes=4,
        filename=None,
    )

    assert stream.media_type == "image/png"
    assert stream.size_bytes == 4
    assert stream.filename is None
