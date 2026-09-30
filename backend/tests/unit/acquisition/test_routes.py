from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from parserium_collector.features.acquisition.errors import (
    CollectionJobNotFoundError,
    CollectionRetryConflictError,
    DocumentNotFoundError,
    ExportUnavailableError,
)
from parserium_collector.features.acquisition.models import (
    CollectionBatchRequest,
    CollectionJobRecord,
    CollectionJobStatus,
    DocumentExportRecord,
    DocumentType,
    ExportStatus,
    StoredDocumentRecord,
)
from parserium_collector.features.acquisition.pagination import Page, PageCursor
from parserium_collector.features.acquisition.router import router
from parserium_collector.features.analysis.models import AnalysisCollectionRequest
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
from parserium_collector.features.storage.access import SignedArtifactDownload
from parserium_collector.features.storage.errors import ArtifactStorageUnavailableError
from parserium_collector.features.storage.models import ArtifactStream
from parserium_collector.security import LocalRequestGuardMiddleware
from parserium_collector.settings import Settings

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
TEST_SESSION = "acquisition-session"
TEST_CSRF = "acquisition-csrf"
ORIGIN = {"Origin": "http://127.0.0.1:8080"}
WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000002")


def job_record(*, workspace_id: UUID = WORKSPACE_A) -> CollectionJobRecord:
    return CollectionJobRecord(
        id=uuid4(),
        workspace_id=workspace_id,
        source_url="https://bank.example/report.pdf",
        title="Bank report",
        expected_document_type=DocumentType.PDF,
        status=CollectionJobStatus.QUEUED,
        attempt_count=0,
        available_at=NOW,
        claimed_by=None,
        lease_expires_at=None,
        bytes_downloaded=0,
        content_length=None,
        document_id=None,
        error_code=None,
        error_detail=None,
        error_retryable=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=None,
        completed_at=None,
    )


def document_record(*, workspace_id: UUID = WORKSPACE_A) -> StoredDocumentRecord:
    return StoredDocumentRecord(
        id=uuid4(),
        workspace_id=workspace_id,
        sha256="a" * 64,
        document_type=DocumentType.PDF,
        media_type="application/pdf",
        size_bytes=1024,
        safe_filename="bank-report.pdf",
        created_at=NOW,
    )


class RouteSessionService:
    def __init__(self, workspace_id: UUID = WORKSPACE_A) -> None:
        self.workspace_id = workspace_id

    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        if token != TEST_SESSION:
            raise InvalidSession
        return AuthenticatedSession(
            token_digest="a" * 64,
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


class RouteAcquisitionService:
    def __init__(self) -> None:
        self.job = job_record()
        self.document = document_record()
        self.export = DocumentExportRecord(
            id=uuid4(),
            workspace_id=WORKSPACE_A,
            document_id=self.document.id,
            relative_directory="bank/2026",
            target_filename=self.document.safe_filename,
            exported_relative_path=None,
            status=ExportStatus.QUEUED,
            attempt_count=0,
            available_at=NOW,
            claimed_by=None,
            lease_expires_at=None,
            error_code=None,
            error_detail=None,
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
        )
        self.missing_job_id = uuid4()
        self.conflict_job_id = uuid4()
        self.missing_document_id = uuid4()
        self.storage_unavailable_id = uuid4()
        self.export_available = True
        self.fail_listing = False
        self.clear_completed_calls = 0
        self.signed_download = False
        self.deleted_document_ids: set[UUID] = set()

    async def create_collection(
        self,
        scope: WorkspaceScope,
        request: CollectionBatchRequest,
        now: datetime,
    ) -> tuple[CollectionJobRecord, ...]:
        assert scope.workspace_id == WORKSPACE_A
        assert request.candidates[0].title == "Bank report"
        return (self.job,)

    async def list_collection_jobs(
        self,
        scope: WorkspaceScope,
        limit: int,
    ) -> tuple[CollectionJobRecord, ...]:
        assert scope.workspace_id == WORKSPACE_A
        if self.fail_listing:
            raise RuntimeError("test-only database detail")
        return (self.job,)[:limit]

    async def list_collection_jobs_page(
        self,
        scope: WorkspaceScope,
        limit: int,
        cursor: PageCursor | None,
    ) -> Page[CollectionJobRecord]:
        assert scope.workspace_id == WORKSPACE_A
        if self.fail_listing:
            raise RuntimeError("test-only database detail")
        assert cursor is None
        items = (self.job,)[:limit]
        return Page(items=items, total=1, next_cursor=None)

    async def retry_collection(
        self,
        scope: WorkspaceScope,
        job_id: UUID,
        now: datetime,
    ) -> CollectionJobRecord:
        assert scope.workspace_id == WORKSPACE_A
        if job_id == self.missing_job_id:
            raise CollectionJobNotFoundError
        if job_id == self.conflict_job_id:
            raise CollectionRetryConflictError
        return self.job

    async def clear_completed_collection_jobs(self, scope: WorkspaceScope) -> int:
        assert scope.workspace_id == WORKSPACE_A
        self.clear_completed_calls += 1
        return 2

    async def list_documents(
        self,
        scope: WorkspaceScope,
        limit: int,
    ) -> tuple[StoredDocumentRecord, ...]:
        assert scope.workspace_id == WORKSPACE_A
        records = () if self.document.id in self.deleted_document_ids else (self.document,)
        return records[:limit]

    async def list_documents_page(
        self,
        scope: WorkspaceScope,
        limit: int,
        cursor: PageCursor | None,
    ) -> Page[StoredDocumentRecord]:
        assert scope.workspace_id == WORKSPACE_A
        assert cursor is None
        records = () if self.document.id in self.deleted_document_ids else (self.document,)
        return Page(items=records[:limit], total=len(records), next_cursor=None)

    async def download_document(self, scope: WorkspaceScope, document_id: UUID):
        assert scope.workspace_id == WORKSPACE_A
        if (
            scope.workspace_id != WORKSPACE_A
            or document_id == self.missing_document_id
            or document_id in self.deleted_document_ids
        ):
            raise DocumentNotFoundError
        if document_id == self.storage_unavailable_id:
            raise ArtifactStorageUnavailableError("test-only provider detail")

        async def body():
            yield b"x" * self.document.size_bytes

        @dataclass(frozen=True)
        class TestDownload:
            document: StoredDocumentRecord
            artifact: ArtifactStream | SignedArtifactDownload

        artifact: ArtifactStream | SignedArtifactDownload
        if self.signed_download:
            artifact = SignedArtifactDownload(
                target="https://storage.test/private-signed-download",
                media_type=self.document.media_type,
                size_bytes=self.document.size_bytes,
                filename=self.document.safe_filename,
            )
        else:
            artifact = ArtifactStream(
                body=body(),
                media_type=self.document.media_type,
                size_bytes=self.document.size_bytes,
                filename=self.document.safe_filename,
            )
        return TestDownload(self.document, artifact)

    async def delete_document(
        self,
        scope: WorkspaceScope,
        document_id: UUID,
        now: datetime,
    ) -> None:
        if scope.workspace_id != WORKSPACE_A or document_id == self.missing_document_id:
            raise DocumentNotFoundError
        self.deleted_document_ids.add(document_id)

    async def create_export(
        self,
        scope: WorkspaceScope,
        document_id: UUID,
        relative_directory: str,
        now: datetime,
    ) -> DocumentExportRecord:
        assert scope.workspace_id == WORKSPACE_A
        if not self.export_available:
            raise ExportUnavailableError
        if document_id == self.missing_document_id:
            raise DocumentNotFoundError
        assert relative_directory == "bank/2026"
        return self.export

    async def list_exports(
        self,
        scope: WorkspaceScope,
        limit: int,
    ) -> tuple[DocumentExportRecord, ...]:
        assert scope.workspace_id == WORKSPACE_A
        return (self.export,)[:limit]


class RouteAnalysisCollectionService:
    def __init__(self, job: CollectionJobRecord) -> None:
        self.job = job
        self.workspace_id: UUID | None = None

    async def collect_candidates(
        self,
        scope: WorkspaceScope,
        request: AnalysisCollectionRequest,
        now: datetime,
    ) -> tuple[CollectionJobRecord, ...]:
        self.workspace_id = scope.workspace_id
        assert len(request.analysis_ids) == 1
        return (
            CollectionJobRecord(
                **{
                    **self.job.__dict__,
                    "status": CollectionJobStatus.COMPLETED,
                    "document_id": uuid4(),
                    "bytes_downloaded": 1024,
                    "content_length": 1024,
                    "completed_at": now,
                }
            ),
        )


def route_app(
    service: object | None,
    analysis_service: object | None = None,
    workspace_id: UUID = WORKSPACE_A,
) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings()
    app.state.session_service = RouteSessionService(workspace_id)
    app.state.acquisition_service = service
    app.state.analysis_service = analysis_service
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


def csrf_headers() -> dict[str, str]:
    return {**ORIGIN, "X-Parserium-CSRF": TEST_CSRF}


def test_collection_reads_require_session_and_mutations_require_csrf() -> None:
    app = route_app(RouteAcquisitionService())
    payload = {
        "candidates": [
            {
                "url": "https://bank.example/report.pdf",
                "title": "Bank report",
                "document_type": "pdf",
            }
        ]
    }
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        missing_session = client.get("/api/v1/collection/jobs")
        client.cookies.set("parserium_session", TEST_SESSION)
        missing_csrf = client.post(
            "/api/v1/collection/jobs",
            json=payload,
            headers=ORIGIN,
        )
    with TestClient(
        route_app(None),
        base_url="http://127.0.0.1:8080",
    ) as client:
        unconfigured_without_session = client.get("/api/v1/collection/jobs")

    assert missing_session.status_code == 401
    assert missing_csrf.status_code == 403
    assert unconfigured_without_session.status_code == 401


def test_collection_jobs_can_be_created_and_listed_without_worker_identity() -> None:
    app = route_app(RouteAcquisitionService())
    payload = {
        "candidates": [
            {
                "url": "https://bank.example/report.pdf",
                "title": "Bank report",
                "document_type": "pdf",
            }
        ]
    }
    with authenticated_client(app) as client:
        created = client.post(
            "/api/v1/collection/jobs",
            json=payload,
            headers=csrf_headers(),
        )
        listed = client.get("/api/v1/collection/jobs?limit=10")

    assert created.status_code == 202
    assert listed.status_code == 200
    assert created.json() == listed.json()["items"]
    assert listed.json()["total"] == 1
    assert listed.json()["next_cursor"] is None
    assert created.json()[0]["status"] == "queued"
    assert "claimed_by" not in created.json()[0]
    assert "lease_expires_at" not in created.json()[0]


def test_analyzed_candidates_are_collected_without_a_second_download() -> None:
    acquisition = RouteAcquisitionService()
    analysis = RouteAnalysisCollectionService(acquisition.job)
    app = route_app(acquisition, analysis)
    analysis_id = uuid4()

    with authenticated_client(app) as client:
        created = client.post(
            "/api/v1/collection/jobs",
            json={"analysis_ids": [str(analysis_id)]},
            headers=csrf_headers(),
        )

    assert created.status_code == 202
    assert created.json()[0]["status"] == "completed"
    assert created.json()[0]["document_id"] is not None
    assert analysis.workspace_id == WORKSPACE_A


def test_collection_batch_bounds_return_validation_details() -> None:
    app = route_app(RouteAcquisitionService())
    with authenticated_client(app) as client:
        empty = client.post(
            "/api/v1/collection/jobs",
            json={"candidates": []},
            headers=csrf_headers(),
        )
        too_many = client.post(
            "/api/v1/collection/jobs",
            json={
                "candidates": [
                    {"url": f"https://bank.example/{index}.pdf", "document_type": "pdf"}
                    for index in range(31)
                ]
            },
            headers=csrf_headers(),
        )

    assert empty.status_code == 422
    assert too_many.status_code == 422
    assert empty.json()["detail"]
    assert too_many.json()["detail"]


def test_stored_document_download_is_authenticated_and_forces_attachment() -> None:
    service = RouteAcquisitionService()
    app = route_app(service)
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        unauthenticated = client.get(f"/api/v1/documents/{service.document.id}/download")
    with authenticated_client(app) as client:
        downloaded = client.get(f"/api/v1/documents/{service.document.id}/download")
        missing = client.get(f"/api/v1/documents/{service.missing_document_id}/download")

    assert unauthenticated.status_code == 401
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert "bank-report.pdf" in downloaded.headers["content-disposition"]
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert downloaded.headers["content-length"] == "1024"
    assert downloaded.headers["referrer-policy"] == "no-referrer"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert missing.status_code == 404


def test_hosted_document_download_redirects_without_exposing_json() -> None:
    service = RouteAcquisitionService()
    service.signed_download = True
    app = route_app(service)

    with authenticated_client(app) as client:
        downloaded = client.get(
            f"/api/v1/documents/{service.document.id}/download",
            follow_redirects=False,
        )

    assert downloaded.status_code == 307
    assert downloaded.headers["location"] == "https://storage.test/private-signed-download"
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert downloaded.headers["referrer-policy"] == "no-referrer"
    assert downloaded.content == b""


def test_document_storage_failure_is_a_sanitized_unavailable_response() -> None:
    service = RouteAcquisitionService()
    app = route_app(service)

    with authenticated_client(app) as client:
        unavailable = client.get(f"/api/v1/documents/{service.storage_unavailable_id}/download")

    assert unavailable.status_code == 503
    assert "provider" not in unavailable.text
    assert "test-only" not in unavailable.text


def test_retry_maps_missing_and_ineligible_jobs() -> None:
    service = RouteAcquisitionService()
    app = route_app(service)
    with authenticated_client(app) as client:
        missing = client.post(
            f"/api/v1/collection/jobs/{service.missing_job_id}/retry",
            headers=csrf_headers(),
        )
        conflict = client.post(
            f"/api/v1/collection/jobs/{service.conflict_job_id}/retry",
            headers=csrf_headers(),
        )

    assert missing.status_code == 404
    assert conflict.status_code == 409


def test_clear_completed_jobs_requires_csrf_and_returns_no_content() -> None:
    service = RouteAcquisitionService()
    app = route_app(service)
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        missing_session = client.delete(
            "/api/v1/collection/jobs/completed",
            headers=ORIGIN,
        )
        client.cookies.set("parserium_session", TEST_SESSION)
        missing_csrf = client.delete(
            "/api/v1/collection/jobs/completed",
            headers=ORIGIN,
        )
        cleared = client.delete(
            "/api/v1/collection/jobs/completed",
            headers=csrf_headers(),
        )

    assert missing_session.status_code == 401
    assert missing_csrf.status_code == 403
    assert cleared.status_code == 204
    assert cleared.content == b""
    assert service.clear_completed_calls == 1


def test_documents_and_exports_use_safe_public_views() -> None:
    service = RouteAcquisitionService()
    app = route_app(service)
    with authenticated_client(app) as client:
        documents = client.get("/api/v1/documents")
        created = client.post(
            f"/api/v1/documents/{service.document.id}/exports",
            json={"relative_directory": "bank/2026"},
            headers=csrf_headers(),
        )
        exports = client.get("/api/v1/exports")

    assert documents.status_code == 200
    assert documents.json()["total"] == 1
    assert documents.json()["next_cursor"] is None
    assert "storage_key" not in documents.json()["items"][0]
    assert created.status_code == 202
    assert created.json() == exports.json()[0]
    assert "claimed_by" not in created.json()
    assert "lease_expires_at" not in created.json()


def test_document_deletion_requires_csrf_is_idempotent_and_hides_cross_workspace() -> None:
    service = RouteAcquisitionService()
    app = route_app(service)
    path = f"/api/v1/documents/{service.document.id}"
    with authenticated_client(app) as client:
        missing_csrf = client.delete(path, headers=ORIGIN)
        deleted = client.delete(path, headers=csrf_headers())
        deleted_again = client.delete(path, headers=csrf_headers())
        documents = client.get("/api/v1/documents")
        download = client.get(f"{path}/download")
    with authenticated_client(route_app(service, workspace_id=WORKSPACE_B)) as client:
        cross_workspace = client.delete(path, headers=csrf_headers())

    assert missing_csrf.status_code == 403
    assert deleted.status_code == 204
    assert deleted_again.status_code == 204
    assert documents.json()["total"] == 0
    assert download.status_code == 404
    assert cross_workspace.status_code == 404


def test_invalid_listing_cursors_return_safe_validation_errors() -> None:
    app = route_app(RouteAcquisitionService())
    with authenticated_client(app) as client:
        jobs = client.get("/api/v1/collection/jobs?cursor=not-valid!")
        documents = client.get("/api/v1/documents?cursor=not-valid!")

    assert jobs.status_code == 422
    assert documents.status_code == 422
    assert jobs.json()["detail"] == "The pagination cursor is invalid."
    assert documents.json()["detail"] == "The pagination cursor is invalid."


def test_export_maps_missing_document_and_unavailable_configuration() -> None:
    service = RouteAcquisitionService()
    app = route_app(service)
    with authenticated_client(app) as client:
        missing = client.post(
            f"/api/v1/documents/{service.missing_document_id}/exports",
            json={},
            headers=csrf_headers(),
        )
        service.export_available = False
        unavailable = client.post(
            f"/api/v1/documents/{service.document.id}/exports",
            json={},
            headers=csrf_headers(),
        )

    assert missing.status_code == 404
    assert unavailable.status_code == 503


def test_unconfigured_and_internal_failures_are_sanitized() -> None:
    with authenticated_client(route_app(None)) as client:
        unavailable = client.get("/api/v1/collection/jobs")

    service = RouteAcquisitionService()
    service.fail_listing = True
    with TestClient(
        route_app(service),
        base_url="http://127.0.0.1:8080",
        raise_server_exceptions=False,
    ) as client:
        client.cookies.set("parserium_session", TEST_SESSION)
        failed = client.get("/api/v1/collection/jobs")

    assert unavailable.status_code == 503
    assert failed.status_code == 500
    assert "database" not in failed.text
    assert "test-only" not in failed.text
