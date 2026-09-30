from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from parserium_collector.features.activity.errors import (
    ActivityConflictError,
    ActivityNotFoundError,
)
from parserium_collector.features.activity.models import (
    ActivityJobResponse,
    ActivityJobState,
    ActivityJobType,
    ActivityPageResponse,
    ActivitySummaryResponse,
)
from parserium_collector.features.activity.router import router
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

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")
USER_ID = UUID("20000000-0000-4000-8000-000000000001")
JOB_ID = UUID("30000000-0000-4000-8000-000000000001")
SESSION = "activity-session"
CSRF = "activity-csrf"
ORIGIN = {"Origin": "http://127.0.0.1:8080"}
NOW = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)


class RouteSessionService:
    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        if token != SESSION:
            raise InvalidSession
        return AuthenticatedSession(
            token_digest="a" * 64,
            csrf_token=CSRF,
            idle_expires_at=now + timedelta(hours=1),
            authentication_mode=AuthenticationMode.LOCAL,
            scope=WorkspaceScope(WORKSPACE_ID, USER_ID, WorkspaceRole.OWNER),
            workspace_name="Test workspace",
            email="owner@example.test",
            display_name="Owner",
            workspaces=(
                WorkspaceSummary(
                    id=WORKSPACE_ID,
                    name="Test workspace",
                    role=WorkspaceRole.OWNER,
                ),
            ),
        )


class RouteActivityService:
    def __init__(self) -> None:
        self.deleted: tuple[WorkspaceScope, ActivityJobType, UUID] | None = None
        self.delete_error: Exception | None = None

    async def list_activity(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
        cursor: str | None,
        job_type: ActivityJobType | None = None,
        state: ActivityJobState | None = None,
        creator_id: UUID | None = None,
        created_after: datetime | None = None,
    ) -> ActivityPageResponse:
        assert scope.workspace_id == WORKSPACE_ID
        assert limit == 25
        assert cursor == "next-page"
        assert job_type is ActivityJobType.DISCOVERY
        assert state is ActivityJobState.COMPLETED
        assert creator_id == USER_ID
        assert created_after == NOW
        return ActivityPageResponse(
            items=[
                ActivityJobResponse(
                    id=JOB_ID,
                    job_type=ActivityJobType.DISCOVERY,
                    title="Bank reports",
                    subtitle="PDF and DOCX",
                    state=ActivityJobState.COMPLETED,
                    stage="completed",
                    progress_percent=100,
                    created_by_user_id=USER_ID,
                    created_by_name="Owner",
                    related_document_id=None,
                    error_code=None,
                    can_cancel=False,
                    can_retry=False,
                    can_delete=True,
                    created_at=NOW,
                    updated_at=NOW,
                    completed_at=NOW,
                )
            ],
            total=1,
            next_cursor=None,
            summary=ActivitySummaryResponse(active=0, queued=0, failed=0, completed=1),
        )

    async def delete_history(
        self,
        scope: WorkspaceScope,
        job_type: ActivityJobType,
        job_id: UUID,
        now: datetime,
    ) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted = (scope, job_type, job_id)


def activity_app(service: RouteActivityService) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings()
    app.state.session_service = RouteSessionService()
    app.state.activity_service = service
    app.add_middleware(
        LocalRequestGuardMiddleware,
        allowed_hosts=("127.0.0.1",),
        allowed_origins=(ORIGIN["Origin"],),
    )
    app.include_router(router)
    return app


def authenticated_client(service: RouteActivityService) -> TestClient:
    client = TestClient(activity_app(service), base_url=ORIGIN["Origin"])
    client.cookies.set("parserium_session", SESSION)
    return client


def test_activity_list_requires_authentication_and_returns_page() -> None:
    service = RouteActivityService()
    with TestClient(activity_app(service), base_url=ORIGIN["Origin"]) as anonymous:
        unauthenticated = anonymous.get("/api/v1/activity")
    with authenticated_client(service) as client:
        response = client.get(
            "/api/v1/activity",
            params={
                "limit": 25,
                "cursor": "next-page",
                "job_type": "discovery",
                "state": "completed",
                "creator_id": str(USER_ID),
                "created_after": NOW.isoformat(),
            },
        )

    assert unauthenticated.status_code == 401
    assert response.status_code == 200
    assert response.json()["items"][0]["title"] == "Bank reports"
    assert response.json()["summary"] == {
        "active": 0,
        "queued": 0,
        "failed": 0,
        "completed": 1,
    }


def test_activity_delete_requires_csrf_and_deletes_terminal_history() -> None:
    service = RouteActivityService()
    path = f"/api/v1/activity/discovery/{JOB_ID}"
    with authenticated_client(service) as client:
        missing_csrf = client.delete(path, headers=ORIGIN)
        deleted = client.delete(path, headers={**ORIGIN, "X-Parserium-CSRF": CSRF})

    assert missing_csrf.status_code == 403
    assert deleted.status_code == 204
    assert service.deleted is not None
    assert service.deleted[1:] == (ActivityJobType.DISCOVERY, JOB_ID)


def test_activity_delete_maps_hidden_and_conflicting_items() -> None:
    service = RouteActivityService()
    path = f"/api/v1/activity/discovery/{JOB_ID}"
    headers = {**ORIGIN, "X-Parserium-CSRF": CSRF}
    with authenticated_client(service) as client:
        service.delete_error = ActivityNotFoundError("hidden")
        hidden = client.delete(path, headers=headers)
        service.delete_error = ActivityConflictError("active")
        conflict = client.delete(path, headers=headers)

    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "Activity item not found."}
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "Active activity history cannot be deleted."}
