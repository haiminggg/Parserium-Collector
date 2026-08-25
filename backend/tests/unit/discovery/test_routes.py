from datetime import datetime, timedelta

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
from parserium_collector.features.session.errors import InvalidSession
from parserium_collector.features.session.models import AuthenticatedSession
from parserium_collector.security import LocalRequestGuardMiddleware

TEST_SESSION_VALUE = "discovery-session"
TEST_CSRF_VALUE = "discovery-csrf"
ORIGIN = {"Origin": "http://127.0.0.1:8080"}


class DiscoverySessionService:
    """Minimal authenticated-session test double."""

    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        if token != TEST_SESSION_VALUE:
            raise InvalidSession
        return AuthenticatedSession(
            csrf_token=TEST_CSRF_VALUE,
            idle_expires_at=now + timedelta(hours=24),
        )


class SuccessfulDiscoveryService:
    """Minimal successful discovery test double."""

    async def search(self, request: DocumentDiscoveryRequest) -> DocumentDiscoveryResponse:
        assert request.query == "bank report"
        return DocumentDiscoveryResponse(
            provider_search_ids=["search-1"],
            candidates=[
                DocumentCandidate(
                    url="https://bank.example/report.pdf",
                    title="Bank report",
                    document_type=DocumentType.PDF,
                )
            ],
            rejected_non_document_results=1,
        )


class FailingDiscoveryService:
    """Minimal failing discovery test double."""

    async def search(self, request: DocumentDiscoveryRequest) -> DocumentDiscoveryResponse:
        raise FirecrawlAdapterError(code="schema_mismatch", retryable=False)


def discovery_app(service: object | None) -> FastAPI:
    app = FastAPI()
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
