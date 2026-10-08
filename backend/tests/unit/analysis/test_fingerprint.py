import hashlib
import hmac
import json
import re
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

import pytest

from parserium_collector.features.analysis.fingerprint import (
    FINGERPRINT_VERSION,
    fingerprint_request,
)
from parserium_collector.features.analysis.models import (
    AnalysisSearchRequest,
    DiscoverySelection,
    DurableAnalysisSearchRequest,
)
from parserium_collector.features.firecrawl_connections.models import ConnectionType

# Deterministic test inputs, never deployment credentials.
SECRET = bytes(range(32))
WORKSPACE = UUID("00000000-0000-0000-0000-000000000001")
CONNECTION = UUID("00000000-0000-0000-0000-000000000002")
SELECTION = DiscoverySelection(
    connection_id=CONNECTION,
    connection_name="Test connection",
    connection_type=ConnectionType.CLOUD,
    credential_revision=1,
    provider_identity="test-provider-identity",
)
Fingerprint = Callable[[bytes, UUID, AnalysisSearchRequest, DiscoverySelection], str]


@pytest.fixture
def fingerprint() -> Fingerprint:
    return fingerprint_request


def test_fingerprint_matches_canonical_hmac_sha256(fingerprint: Fingerprint) -> None:
    assert FINGERPRINT_VERSION == 1
    request = AnalysisSearchRequest(query="Reports", include_domains=("reports.example",))
    canonical = json.dumps(
        {
            "workspace_id": str(WORKSPACE),
            "connection_id": str(CONNECTION),
            "credential_revision": 1,
            "provider_identity": "test-provider-identity",
            "query": "Reports",
            "limit": 20,
            "document_types": ["docx", "pdf"],
            "include_domains": ["reports.example"],
            "exclude_domains": [],
            "tables_required": True,
            "request_profile_version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    result = fingerprint(SECRET, WORKSPACE, request, SELECTION)

    assert result == hmac.new(SECRET, canonical, hashlib.sha256).hexdigest()
    assert re.fullmatch("[0-9a-f]{64}", result)


@pytest.mark.parametrize("domain_field", ("include_domains", "exclude_domains"))
def test_fingerprint_ignores_order_duplicates_and_outer_query_whitespace(
    fingerprint: Fingerprint, domain_field: str
) -> None:
    first = AnalysisSearchRequest.model_validate(
        {
            "query": "  Quarterly  Reports \n",
            "document_types": ["pdf", "docx", "pdf"],
            domain_field: ["b.example", "a.example", "b.example"],
        }
    )
    second = AnalysisSearchRequest.model_validate(
        {
            "query": "Quarterly  Reports",
            "document_types": ["docx", "pdf"],
            domain_field: ["a.example", "b.example"],
        }
    )

    assert fingerprint(SECRET, WORKSPACE, first, SELECTION) == fingerprint(
        SECRET, WORKSPACE, second, SELECTION
    )


@pytest.mark.parametrize(
    "change",
    (
        {"query": "quarterly  Reports"},
        {"query": "Quarterly Reports"},
        {"limit": 19},
        {"document_types": ["pdf"]},
        {"include_domains": ["a.example"]},
        {"exclude_domains": ["a.example"]},
        {"tables_required": False},
    ),
)
def test_fingerprint_changes_with_request_semantics(
    fingerprint: Fingerprint, change: dict[str, object]
) -> None:
    request = AnalysisSearchRequest(query="Quarterly  Reports")
    changed = AnalysisSearchRequest.model_validate({**request.model_dump(), **change})

    assert fingerprint(SECRET, WORKSPACE, request, SELECTION) != fingerprint(
        SECRET, WORKSPACE, changed, SELECTION
    )


@pytest.mark.parametrize(
    "selection",
    (
        replace(SELECTION, connection_id=UUID("00000000-0000-0000-0000-000000000003")),
        replace(SELECTION, connection_id=None),
        replace(SELECTION, credential_revision=2),
        replace(SELECTION, credential_revision=None),
        replace(SELECTION, provider_identity="another-test-provider"),
    ),
)
def test_fingerprint_changes_with_resolved_provider_identity(
    fingerprint: Fingerprint, selection: DiscoverySelection
) -> None:
    request = AnalysisSearchRequest(query="Reports")

    assert fingerprint(SECRET, WORKSPACE, request, SELECTION) != fingerprint(
        SECRET, WORKSPACE, request, selection
    )


def test_fingerprint_is_scoped_to_workspace_and_secret(fingerprint: Fingerprint) -> None:
    request = AnalysisSearchRequest(query="Reports")
    original = fingerprint(SECRET, WORKSPACE, request, SELECTION)

    assert original != fingerprint(bytes(reversed(SECRET)), WORKSPACE, request, SELECTION)
    assert original != fingerprint(SECRET, CONNECTION, request, SELECTION)


def test_fingerprint_uses_resolved_not_requested_connection(fingerprint: Fingerprint) -> None:
    implicit = AnalysisSearchRequest(query="Reports")
    explicit = AnalysisSearchRequest(query="Reports", firecrawl_connection_id=CONNECTION)

    assert fingerprint(SECRET, WORKSPACE, implicit, SELECTION) == fingerprint(
        SECRET, WORKSPACE, explicit, SELECTION
    )


def test_fingerprint_excludes_connection_display_name_and_refresh(
    fingerprint: Fingerprint,
) -> None:
    request = DurableAnalysisSearchRequest(query="Reports")
    refreshed = DurableAnalysisSearchRequest(query="Reports", force_refresh=True)
    renamed = replace(SELECTION, connection_name="Renamed test connection")

    assert fingerprint(SECRET, WORKSPACE, request, SELECTION) == fingerprint(
        SECRET, WORKSPACE, refreshed, renamed
    )


def test_fingerprint_supports_deployment_default_selection(fingerprint: Fingerprint) -> None:
    selection = DiscoverySelection(
        connection_id=None,
        connection_name=None,
        connection_type=None,
        credential_revision=None,
        provider_identity="test-deployment-default",
    )
    result = fingerprint(SECRET, WORKSPACE, AnalysisSearchRequest(query="Reports"), selection)

    assert re.fullmatch("[0-9a-f]{64}", result)


@pytest.mark.parametrize("length", (0, 1, 16, 31))
def test_fingerprint_rejects_short_secrets(fingerprint: Fingerprint, length: int) -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        fingerprint(bytes(length), WORKSPACE, AnalysisSearchRequest(query="Reports"), SELECTION)
