from datetime import datetime, timedelta
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.discovery.models import (
    DocumentCandidate,
    DocumentDiscoveryRequest,
    DocumentDiscoveryResponse,
    DocumentType,
)
from parserium_collector.features.discovery.router import router
from parserium_collector.features.discovery.scoped import ScopedDiscoveryResult
from parserium_collector.features.firecrawl_connections.models import ConnectionType
from parserium_collector.features.identity.models import (
    AuthenticationMode,
    WorkspaceRole,
    WorkspaceScope,
)
from parserium_collector.features.session.errors import InvalidSession
from parserium_collector.features.session.models import (
    AuthenticatedSession,
    WorkspaceSummary,
)
from parserium_collector.security import LocalRequestGuardMiddleware
from parserium_collector.settings import Settings

TEST_SESSION_VALUE = "discovery-session"
TEST_CSRF_VALUE = "discovery-csrf"
ORIGIN = {"Origin": "http://127.0.0.1:8080"}
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")


class DiscoverySessionService:
    """Minimal authenticated-session test double."""

    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        if token != TEST_SESSION_VALUE:
            raise InvalidSession
        return AuthenticatedSession(
            token_digest="a" * 64,
            csrf_token=TEST_CSRF_VALUE,
            idle_expires_at=now + timedelta(hours=24),
            authentication_mode=AuthenticationMode.LOCAL,
            scope=WorkspaceScope(
                workspace_id=WORKSPACE_ID,
                user_id=None,
                role=WorkspaceRole.OWNER,
            ),
            workspace_name="Local workspace",
            email=None,
            display_name=None,
            workspaces=(
                WorkspaceSummary(
                    id=WORKSPACE_ID,
                    name="Local workspace",
                    role=WorkspaceRole.OWNER,
                ),
            ),
        )


class SuccessfulDiscoveryService:
    """Minimal successful discovery test double."""

    async def search(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
        now: datetime,
    ) -> ScopedDiscoveryResult:
        assert scope.workspace_id == WORKSPACE_ID
        assert request.query == "bank report"
        return ScopedDiscoveryResult(
            response=DocumentDiscoveryResponse(
                provider_search_ids=["search-1"],
                candidates=[
                    DocumentCandidate(
                        url="https://bank.example/report.pdf",
                        title="Bank report",
                        document_type=DocumentType.PDF,
                    )
                ],
                rejected_non_document_results=1,
            ),
            connection_id=request.firecrawl_connection_id,
            connection_name_snapshot="Selected" if request.firecrawl_connection_id else None,
            connection_type_snapshot=(
                ConnectionType.CLOUD if request.firecrawl_connection_id else None
            ),
        )


class FailingDiscoveryService:
    """Minimal failing discovery test double."""

    async def search(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
        now: datetime,
    ) -> ScopedDiscoveryResult:
        raise FirecrawlAdapterError(code="schema_mismatch", retryable=False)


def discovery_app(service: object | None) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings()
    app.state.session_service = DiscoverySessionService()
    app.state.discovery_service = service
    app.add_middleware(
        LocalRequestGuardMiddleware,
        allowed_hosts=("127.0.0.1",),
        allowed_origins=("http://127.0.0.1:8080",),
    )
    app.include_router(router)
    return app


def authenticated_client(app: FastAPI) -> TestClient:
    client = TestClient(app, base_url="http://127.0.0.1:8080")
    client.cookies.set("parserium_session", TEST_SESSION_VALUE)
    return client


def test_discovery_search_requires_session_and_csrf() -> None:
    app = discovery_app(SuccessfulDiscoveryService())
    payload = {"query": "bank report", "document_types": ["pdf"]}
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        missing_session = client.post(
            "/api/v1/discovery/search",
            json=payload,
            headers=ORIGIN,
        )
        client.cookies.set("parserium_session", TEST_SESSION_VALUE)
        missing_csrf = client.post(
            "/api/v1/discovery/search",
            json=payload,
            headers=ORIGIN,
        )

    assert missing_session.status_code == 401
    assert missing_csrf.status_code == 403


def test_discovery_search_returns_direct_document_candidates() -> None:
    app = discovery_app(SuccessfulDiscoveryService())
    with authenticated_client(app) as client:
        response = client.post(
            "/api/v1/discovery/search",
            json={"query": "bank report", "document_types": ["pdf"]},
            headers={**ORIGIN, "X-Parserium-CSRF": TEST_CSRF_VALUE},
        )

    assert response.status_code == 200
    assert response.json() == {
        "provider_search_ids": ["search-1"],
        "candidates": [
            {
                "url": "https://bank.example/report.pdf",
                "title": "Bank report",
                "description": None,
                "document_type": "pdf",
                "source": "firecrawl",
            }
        ],
        "rejected_non_document_results": 1,
    }


def test_discovery_search_reports_unconfigured_and_upstream_failures_generically() -> None:
    headers = {**ORIGIN, "X-Parserium-CSRF": TEST_CSRF_VALUE}
    with authenticated_client(discovery_app(None)) as client:
        unconfigured = client.post(
            "/api/v1/discovery/search",
            json={"query": "bank report"},
            headers=headers,
        )
    with authenticated_client(discovery_app(FailingDiscoveryService())) as client:
        failed = client.post(
            "/api/v1/discovery/search",
            json={"query": "bank report"},
            headers=headers,
        )

    assert unconfigured.status_code == 503
    assert unconfigured.json() == {"detail": "Document discovery is not configured."}
    assert failed.status_code == 502
    assert failed.json() == {"detail": "Document discovery is unavailable."}
