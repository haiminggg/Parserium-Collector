import re
from uuid import UUID

from parserium_collector.features.storage.errors import InvalidStorageKeyError
from parserium_collector.features.storage.models import ArtifactKind, ArtifactResourceKind

SAFE_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def validate_storage_key(key: str) -> str:
    if not key or key.startswith("/") or key.endswith("/") or "\\" in key:
        raise InvalidStorageKeyError("The artifact storage key is invalid.")
    if any(ord(character) < 32 or ord(character) == 127 for character in key):
        raise InvalidStorageKeyError("The artifact storage key is invalid.")
    segments = key.split("/")
    if any(not segment or segment in {".", ".."} for segment in segments):
        raise InvalidStorageKeyError("The artifact storage key is invalid.")
    return key


def artifact_key(
    workspace_id: UUID,
    resource_kind: ArtifactResourceKind,
    resource_id: UUID,
    artifact_kind: ArtifactKind,
) -> str:
    return validate_storage_key(
        f"workspaces/{workspace_id}/{resource_kind.value}/{resource_id}/{artifact_kind.value}"
    )


def health_probe_key(instance_id: str) -> str:
    if SAFE_SEGMENT_PATTERN.fullmatch(instance_id) is None or instance_id in {".", ".."}:
        raise InvalidStorageKeyError("The storage probe instance identifier is invalid.")
    return validate_storage_key(f"internal/health/{instance_id}")
