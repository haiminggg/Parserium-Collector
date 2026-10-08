from uuid import UUID

import pytest

from parserium_collector.features.storage.errors import InvalidStorageKeyError
from parserium_collector.features.storage.keys import (
    artifact_key,
    health_probe_key,
    validate_storage_key,
)
from parserium_collector.features.storage.models import ArtifactKind, ArtifactResourceKind

WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_artifact_key_uses_only_fixed_enums_and_uuids() -> None:
    assert artifact_key(
        WORKSPACE_ID,
        ArtifactResourceKind.DOCUMENT,
        DOCUMENT_ID,
        ArtifactKind.STORED_DOCUMENT,
    ) == (
        "workspaces/00000000-0000-0000-0000-000000000001/document/"
        "00000000-0000-0000-0000-000000000002/stored_document"
    )


@pytest.mark.parametrize(
    "key",
    [
        "",
        "../secret",
        "/absolute",
        "workspaces//object",
        "workspaces/./object",
        "workspaces/../object",
        "workspaces\\object",
        "workspaces/object/",
        "workspaces/object\x00",
        "workspaces/object\nnext",
    ],
)
def test_validate_storage_key_rejects_uncontained_values(key: str) -> None:
    with pytest.raises(InvalidStorageKeyError):
        validate_storage_key(key)


def test_validate_storage_key_returns_a_safe_key_unchanged() -> None:
    key = "workspaces/00000000-0000-0000-0000-000000000001/document/object/source_pdf"

    assert validate_storage_key(key) == key


def test_health_probe_key_rejects_an_unsafe_instance_identifier() -> None:
    with pytest.raises(InvalidStorageKeyError):
        health_probe_key("../api")


def test_health_probe_key_uses_the_internal_prefix() -> None:
    assert health_probe_key("api-1") == "internal/health/api-1"
