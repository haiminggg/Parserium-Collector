from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.analysis.errors import (
    AnalysisExpiredError,
    AnalysisNotFoundError,
    AnalysisPreviewUnavailableError,
)
from parserium_collector.features.analysis.models import (
    AnalysisCandidateResponse,
    AnalysisSearchRequest,
    AnalysisSessionResponse,
)
from parserium_collector.features.analysis.router import router
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
from parserium_collector.features.storage.errors import ArtifactStorageUnavailableError
from parserium_collector.features.storage.models import ArtifactStream
from parserium_collector.security import LocalRequestGuardMiddleware
from parserium_collector.settings import Settings

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
TEST_SESSION = "analysis-session"
TEST_CSRF = "analysis-csrf"
OWNER = "a" * 64
WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000002")
USER_A = UUID("20000000-0000-4000-8000-000000000001")
CONNECTION_A = UUID("30000000-0000-4000-8000-000000000001")
ORIGIN = {"Origin": "http://127.0.0.1:8080"}


def response_snapshot() -> AnalysisSessionResponse:
    candidate = AnalysisCandidateResponse(
        id=uuid4(),
        ordinal=0,
        source_url="https://bank.example/report.pdf",
        title="Bank report",
        description="Investment tables",
        document_type="pdf",
        status="queued",
        public_state=None,
        attempt_count=0,
        bytes_downloaded=0,
        content_length=None,
        page_count=None,
        analyzed_page_count=0,
        table_count=0,
        table_count_lower_bound=False,
        preview_available=False,
        preview_page_num=None,
        preview_width=None,
        preview_height=None,
        error_code=None,
        error_detail=None,
        error_retryable=None,
        tables=(),
        created_at=NOW,
        updated_at=NOW,
        completed_at=None,
    )
    return AnalysisSessionResponse(
        id=uuid4(),
        query="bank report",
        document_types=("pdf",),
        tables_required=True,
        firecrawl_connection_id=CONNECTION_A,
        firecrawl_connection_name_snapshot="Workspace cloud",
        firecrawl_connection_type_snapshot="cloud",
        status="queued",
        job_stage="queued",
        error_code=None,
        candidate_count=1,
        bytes_downloaded=0,
        session_byte_limit=4096,
        cancellation_requested=False,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        completed_at=None,
        candidates=(candidate,),
    )


class RouteSessionService:
    def __init__(self, workspace_id: UUID = WORKSPACE_A) -> None:
        self.workspace_id = workspace_id

    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        if token != TEST_SESSION:
            raise InvalidSession
        return AuthenticatedSession(
            token_digest=OWNER,
            csrf_token=TEST_CSRF,
            idle_expires_at=now + timedelta(hours=24),
            authentication_mode=AuthenticationMode.LOCAL,
            scope=WorkspaceScope(
                workspace_id=self.workspace_id,
                user_id=None,
                role=WorkspaceRole.OWNER,
            ),
            workspace_name="Local workspace",
            email=None,
            display_name=None,
            workspaces=(
                WorkspaceSummary(
                    id=self.workspace_id,
                    name="Local workspace",
                    role=WorkspaceRole.OWNER,
                ),
            ),
        )


class RouteAnalysisService:
    def __init__(self, preview: Path) -> None:
        self.snapshot = response_snapshot()
        self.preview_content = (
            preview.read_bytes() if preview.is_file() else b"\x89PNG\r\n\x1a\ntest-only preview"
        )
        self.cancel_calls = 0
        self.retry_calls = 0
        self.not_found_id = uuid4()
        self.expired_id = uuid4()
        self.unavailable_preview_id = uuid4()
        self.storage_unavailable_id = uuid4()

    async def start_search(
        self,
        scope: WorkspaceScope,
        request: AnalysisSearchRequest,
        now: datetime,
    ) -> AnalysisSessionResponse:
        assert scope.workspace_id == WORKSPACE_A
        assert request.tables_required is True
        assert request.firecrawl_connection_id == CONNECTION_A
        return self.snapshot

    async def get_search(
        self,
        scope: WorkspaceScope,
        session_id: UUID,
        now: datetime,
    ) -> AnalysisSessionResponse:
        if scope.workspace_id != WORKSPACE_A or session_id == self.not_found_id:
            raise AnalysisNotFoundError
        if session_id == self.expired_id:
            raise AnalysisExpiredError
        return self.snapshot

    async def cancel_search(
        self,
        scope: WorkspaceScope,
        session_id: UUID,
        now: datetime,
    ) -> bool:
        if scope.workspace_id != WORKSPACE_A or session_id == self.not_found_id:
            raise AnalysisNotFoundError
        self.cancel_calls += 1
        return True

    async def retry_search(
        self,
        scope: WorkspaceScope,
        session_id: UUID,
        now: datetime,
    ) -> AnalysisSessionResponse:
        if scope.workspace_id != WORKSPACE_A or session_id == self.not_found_id:
            raise AnalysisNotFoundError
        self.retry_calls += 1
        return self.snapshot

    async def preview_stream(
        self,
        scope: WorkspaceScope,
        candidate_id: UUID,
        now: datetime,
    ) -> ArtifactStream:
        if scope.workspace_id != WORKSPACE_A or candidate_id == self.not_found_id:
            raise AnalysisNotFoundError
        if candidate_id == self.expired_id:
            raise AnalysisExpiredError
        if candidate_id == self.unavailable_preview_id:
            raise AnalysisPreviewUnavailableError
        if candidate_id == self.storage_unavailable_id:
            raise ArtifactStorageUnavailableError("test-only provider detail")

        async def body():
            yield self.preview_content

        return ArtifactStream(
            body=body(),
            media_type="image/png",
            size_bytes=len(self.preview_content),
            filename=None,
        )


class FirecrawlFailingAnalysisService(RouteAnalysisService):
    async def start_search(
        self,
        scope: WorkspaceScope,
        request: AnalysisSearchRequest,
        now: datetime,
    ) -> AnalysisSessionResponse:
        raise FirecrawlAdapterError(code="incompatible_response", retryable=False)


def analysis_app(
    service: object | None,
    workspace_id: UUID = WORKSPACE_A,
) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings()
    app.state.session_service = RouteSessionService(workspace_id)
    app.state.analysis_service = service
    app.add_middleware(
        LocalRequestGuardMiddleware,
        allowed_hosts=("127.0.0.1",),
        allowed_origins=("http://127.0.0.1:8080",),
    )
    app.include_router(router)
    return app


def authenticated_client(app: FastAPI) -> TestClient:
    client = TestClient(app, base_url="http://127.0.0.1:8080")
    client.cookies.set("parserium_session", TEST_SESSION)
    return client


def test_search_creation_requires_session_and_csrf(tmp_path: Path) -> None:
    service = RouteAnalysisService(tmp_path / "preview.png")
    app = analysis_app(service)
    payload = {"query": "bank report", "document_types": ["pdf"]}
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        missing_session = client.post(
            "/api/v1/discovery/searches",
            json=payload,
            headers=ORIGIN,
        )
        client.cookies.set("parserium_session", TEST_SESSION)
        missing_csrf = client.post(
            "/api/v1/discovery/searches",
            json=payload,
            headers=ORIGIN,
        )

    assert missing_session.status_code == 401
    assert missing_csrf.status_code == 403


def test_search_lifecycle_returns_public_snapshot_and_safe_statuses(tmp_path: Path) -> None:
    preview = tmp_path / "preview.png"
    preview.write_bytes(b"\x89PNG\r\n\x1a\ntest-only preview")
    service = RouteAnalysisService(preview)
    app = analysis_app(service)
    with authenticated_client(app) as client:
        created = client.post(
            "/api/v1/discovery/searches",
            json={
                "query": "bank report",
                "document_types": ["pdf"],
                "tables_required": True,
                "firecrawl_connection_id": str(CONNECTION_A),
            },
            headers={**ORIGIN, "X-Parserium-CSRF": TEST_CSRF},
        )
        polled = client.get(
            f"/api/v1/discovery/searches/{service.snapshot.id}",
            headers=ORIGIN,
        )
        expired = client.get(
            f"/api/v1/discovery/searches/{service.expired_id}",
            headers=ORIGIN,
        )
        cancelled = client.delete(
            f"/api/v1/discovery/searches/{service.snapshot.id}",
            headers={**ORIGIN, "X-Parserium-CSRF": TEST_CSRF},
        )
        retried = client.post(
            f"/api/v1/discovery/searches/{service.snapshot.id}/retry",
            headers={**ORIGIN, "X-Parserium-CSRF": TEST_CSRF},
        )

    assert created.status_code == 202
    assert polled.status_code == 200
    assert polled.json()["firecrawl_connection_id"] == str(CONNECTION_A)
    assert polled.json()["firecrawl_connection_name_snapshot"] == "Workspace cloud"
    assert polled.json()["firecrawl_connection_type_snapshot"] == "cloud"
    serialized = str(polled.json())
    assert "storage_key" not in serialized
    assert "claimed_by" not in serialized
    assert expired.status_code == 410
    assert cancelled.status_code == 204
    assert cancelled.content == b""
    assert service.cancel_calls == 1
    assert retried.status_code == 202
    assert retried.json()["id"] == str(service.snapshot.id)
    assert service.retry_calls == 1


def test_search_creation_returns_safe_code_for_firecrawl_failure(tmp_path: Path) -> None:
    service = FirecrawlFailingAnalysisService(tmp_path / "preview.png")
    app = analysis_app(service)
    with authenticated_client(app) as client:
        response = client.post(
            "/api/v1/discovery/searches",
            json={"query": "bank report", "document_types": ["pdf"]},
            headers={**ORIGIN, "X-Parserium-CSRF": TEST_CSRF},
        )

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "firecrawl_discovery_unavailable",
            "message": (
                "Firecrawl document discovery is unavailable. "
                "Test the selected connection and try again."
            ),
        }
    }
    assert "incompatible_response" not in response.text


def test_preview_is_authenticated_png_with_private_cache_policy(tmp_path: Path) -> None:
    preview = tmp_path / "preview.png"
    content = b"\x89PNG\r\n\x1a\ntest-only preview"
    preview.write_bytes(content)
    service = RouteAnalysisService(preview)
    app = analysis_app(service)
    candidate_id = service.snapshot.candidates[0].id

    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        unauthenticated = client.get(
            f"/api/v1/discovery/analyses/{candidate_id}/preview",
            headers=ORIGIN,
        )
    with authenticated_client(app) as client:
        response = client.get(
            f"/api/v1/discovery/analyses/{candidate_id}/preview",
            headers=ORIGIN,
        )
        unavailable = client.get(
            f"/api/v1/discovery/analyses/{service.unavailable_preview_id}/preview",
            headers=ORIGIN,
        )

    assert unauthenticated.status_code == 401
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-length"] == str(len(content))
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == content
    assert unavailable.status_code == 404


def test_preview_expiry_and_provider_failures_are_safely_mapped(tmp_path: Path) -> None:
    preview = tmp_path / "preview.png"
    preview.write_bytes(b"\x89PNG\r\n\x1a\ntest-only preview")
    service = RouteAnalysisService(preview)
    app = analysis_app(service)

    with authenticated_client(app) as client:
        expired = client.get(
            f"/api/v1/discovery/analyses/{service.expired_id}/preview",
        )
        unavailable = client.get(
            f"/api/v1/discovery/analyses/{service.storage_unavailable_id}/preview",
        )

    assert expired.status_code == 410
    assert unavailable.status_code == 503
    assert "provider" not in unavailable.text
    assert "test-only" not in unavailable.text


def test_cross_workspace_session_and_preview_are_indistinguishable_from_missing(
    tmp_path: Path,
) -> None:
    preview = tmp_path / "preview.png"
    preview.write_bytes(b"\x89PNG\r\n\x1a\ntest-only preview")
    service = RouteAnalysisService(preview)
    app = analysis_app(service, WORKSPACE_B)
    with authenticated_client(app) as client:
        session = client.get(f"/api/v1/discovery/searches/{service.snapshot.id}")
        candidate = client.get(
            f"/api/v1/discovery/analyses/{service.snapshot.candidates[0].id}/preview"
        )

    assert session.status_code == 404
    assert candidate.status_code == 404
