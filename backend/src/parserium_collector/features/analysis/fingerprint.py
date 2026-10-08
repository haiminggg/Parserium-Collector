import hashlib
import hmac
import json
from typing import Final
from uuid import UUID

from parserium_collector.features.analysis.models import AnalysisSearchRequest, DiscoverySelection

FINGERPRINT_VERSION: Final[int] = 1


def fingerprint_request(
    secret: bytes,
    workspace: UUID,
    request: AnalysisSearchRequest,
    selection: DiscoverySelection,
) -> str:
    """Identify resolved request semantics without exposing query or credential material."""
    if len(secret) < 32:
        raise ValueError("The request fingerprint secret must contain at least 32 bytes.")

    payload = {
        "workspace_id": str(workspace),
        "connection_id": str(selection.connection_id) if selection.connection_id else None,
        "credential_revision": selection.credential_revision,
        "provider_identity": selection.provider_identity,
        "query": request.query.strip(),
        "limit": request.limit,
        "document_types": sorted(set(request.document_types)),
        "include_domains": sorted(set(request.include_domains)),
        "exclude_domains": sorted(set(request.exclude_domains)),
        "tables_required": request.tables_required,
        "request_profile_version": FINGERPRINT_VERSION,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()
