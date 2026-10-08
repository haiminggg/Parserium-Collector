from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from parserium_collector.features.firecrawl_connections.errors import (
    ConnectionDefaultConflictError,
    ConnectionNameConflictError,
    ConnectionNotFoundError,
    ConnectionPermissionError,
    ConnectionUnavailableError,
    CredentialUnavailableError,
    RemoteEndpointRejectedError,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionListResponse,
    ConnectionSummary,
    ConnectionType,
    CreateConnectionRequest,
    FirecrawlConnectionRecord,
    UpdateConnectionRequest,
)
from parserium_collector.features.firecrawl_connections.router import router
from parserium_collector.features.identity.models import (
    AuthenticationMode,
    HostedSessionNotFound,
    WorkspaceRole,
    WorkspaceScope,
)
from parserium_collector.features.session.models import (
    AuthenticatedSession,
    WorkspaceSummary,
)
from parserium_collector.settings import DeploymentMode, Settings

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
SESSION = "hosted-connection-session"
CSRF = "hosted-connection-csrf"
WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000017")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000027")
USER_A = UUID("20000000-0000-4000-8000-000000000017")
CONNECTION_A = UUID("30000000-0000-4000-8000-000000000017")
MISSING_CONNECTION = UUID("30000000-0000-4000-8000-000000000099")
ORIGIN = {"Origin": "https://app.parserium.test"}


def connection_record() -> FirecrawlConnectionRecord:
    return FirecrawlConnectionRecord(
        id=CONNECTION_A,
        workspace_id=WORKSPACE_A,
        name="Primary remote",
        normalized_name="primary remote",
        connection_type=ConnectionType.REMOTE,
        normalized_base_url="https://firecrawl.example",
        credential_envelope=None,
        credential_revision=1,
        validated_revision=1,
        validation_succeeded=True,
        capability_profile={"contract": "metadata-search-v2"},
        last_validation_attempt_at=NOW,
        last_validation_success_at=NOW,
        last_failure_category=None,
        enabled=True,
        is_default=True,
        created_by_user_id=USER_A,
        updated_by_user_id=USER_A,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
    )


class RouteIdentityService:
    def __init__(self, role: WorkspaceRole) -> None:
        self.role = role

    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        if token != SESSION:
            raise HostedSessionNotFound
        return AuthenticatedSession(
            token_digest="a" * 64,
            csrf_token=CSRF,
            idle_expires_at=now + timedelta(hours=1),
            authentication_mode=AuthenticationMode.OIDC,
            scope=WorkspaceScope(WORKSPACE_A, USER_A, self.role),
            workspace_name="Workspace A",
            email="owner@example.test",
            display_name="Test owner",
            workspaces=(
                WorkspaceSummary(
                    id=WORKSPACE_A,
                    name="Workspace A",
                    role=self.role,
                ),
            ),
        )


class RouteConnectionService:
    """Minimal route-boundary test double. It never handles real credentials."""

    def __init__(self) -> None:
        self.record = connection_record()
        self.error: Exception | None = None
        self.calls: list[str] = []

    def _check(self, scope: WorkspaceScope, operation: str) -> None:
        self.calls.append(operation)
        if self.error is not None:
            raise self.error
        if operation != "list" and scope.role is not WorkspaceRole.OWNER:
            raise ConnectionPermissionError

    async def list_connections(self, scope: WorkspaceScope) -> ConnectionListResponse:
        self._check(scope, "list")
        return ConnectionListResponse(
            connections=(
                ConnectionSummary.from_record(
                    self.record,
                    include_endpoint=scope.role is WorkspaceRole.OWNER,
                ),
            )
        )

    async def create_connection(
        self,
        scope: WorkspaceScope,
        request: CreateConnectionRequest,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        self._check(scope, "create")
        assert request.credential == "test-only-token"
        return self.record

    async def update_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        request: UpdateConnectionRequest,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        self._check(scope, "update")
        assert connection_id == CONNECTION_A
        return replace(self.record, name=request.name or self.record.name)

    async def test_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        self._check(scope, "test")
        assert connection_id == CONNECTION_A
        return self.record

    async def replace_credential(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        credential: str,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        self._check(scope, "credential")
        assert connection_id == CONNECTION_A
        assert credential == "test-only-token"
        return replace(self.record, credential_revision=2, validated_revision=2)

    async def delete_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        now: datetime,
    ) -> None:
        self._check(scope, "delete")
        assert connection_id == CONNECTION_A


def route_app(
    service: RouteConnectionService,
    *,
    role: WorkspaceRole = WorkspaceRole.OWNER,
    hosted: bool = True,
) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings().model_copy(
        update={
            "deployment_mode": (DeploymentMode.HOSTED if hosted else DeploymentMode.SELF_HOSTED)
        }
    )
    app.state.identity_service = RouteIdentityService(role)
    app.state.firecrawl_connection_service = service
    app.include_router(router)
    return app


def authenticated_client(app: FastAPI) -> TestClient:
    client = TestClient(app, base_url="https://app.parserium.test")
    client.cookies.set("parserium_session", SESSION)
    return client


def csrf_headers() -> dict[str, str]:
    return {**ORIGIN, "X-Parserium-CSRF": CSRF}


def assert_no_secret_material(response_text: str) -> None:
    serialized = response_text.lower()
    for forbidden in (
        "test-only-token",
        "credential_envelope",
        "wrapped_data_key",
        "ciphertext",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "post",
            "/api/v1/firecrawl/connections",
            {
                "name": "Primary cloud",
                "connection_type": "cloud",
                "credential": "test-only-token",
            },
        ),
        (
            "patch",
            f"/api/v1/firecrawl/connections/{CONNECTION_A}",
            {"name": "Renamed"},
        ),
        ("post", f"/api/v1/firecrawl/connections/{CONNECTION_A}/test", None),
        (
            "put",
            f"/api/v1/firecrawl/connections/{CONNECTION_A}/credential",
            {"credential": "test-only-token"},
        ),
        ("delete", f"/api/v1/firecrawl/connections/{CONNECTION_A}", None),
    ],
)
def test_reads_require_authentication_and_every_mutation_requires_csrf(
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    service = RouteConnectionService()
    app = route_app(service)
    with TestClient(app, base_url="https://app.parserium.test") as client:
        missing_session = client.get("/api/v1/firecrawl/connections")
        client.cookies.set("parserium_session", SESSION)
        missing_csrf = client.request(method, path, json=payload, headers=ORIGIN)

    assert missing_session.status_code == 401
    assert missing_csrf.status_code == 403
    assert service.calls == []


def test_owner_can_use_all_routes_and_responses_never_serialize_credentials() -> None:
    service = RouteConnectionService()
    with authenticated_client(route_app(service)) as client:
        responses = (
            client.get("/api/v1/firecrawl/connections"),
            client.post(
                "/api/v1/firecrawl/connections",
                json={
                    "name": "Primary cloud",
                    "connection_type": "cloud",
                    "credential": "test-only-token",
                },
                headers=csrf_headers(),
            ),
            client.patch(
                f"/api/v1/firecrawl/connections/{CONNECTION_A}",
                json={"name": "Renamed"},
                headers=csrf_headers(),
            ),
            client.post(
                f"/api/v1/firecrawl/connections/{CONNECTION_A}/test",
                headers=csrf_headers(),
            ),
            client.put(
                f"/api/v1/firecrawl/connections/{CONNECTION_A}/credential",
                json={"credential": "test-only-token"},
                headers=csrf_headers(),
            ),
            client.delete(
                f"/api/v1/firecrawl/connections/{CONNECTION_A}",
                headers=csrf_headers(),
            ),
        )

    assert [response.status_code for response in responses] == [200, 201, 200, 200, 200, 204]
    for response in responses:
        assert_no_secret_material(response.text)
    for response in responses[:-1]:
        payload = response.json()
        summary = payload["connections"][0] if "connections" in payload else payload
        assert "last_failure_category" in summary
        assert summary["last_failure_category"] is None


def test_member_list_omits_remote_endpoint_and_mutations_are_forbidden() -> None:
    service = RouteConnectionService()
    with authenticated_client(route_app(service, role=WorkspaceRole.MEMBER)) as client:
        listed = client.get("/api/v1/firecrawl/connections")
        mutation = client.post(
            f"/api/v1/firecrawl/connections/{CONNECTION_A}/test",
            headers=csrf_headers(),
        )

    assert listed.status_code == 200
    member_summary = listed.json()["connections"][0]
    assert "normalized_base_url" not in member_summary
    assert "last_failure_category" in member_summary
    assert member_summary["last_failure_category"] is None
    assert mutation.status_code == 403
    assert mutation.json()["detail"] == {
        "code": "connection_permission_denied",
        "message": "Workspace owner access is required.",
    }
    assert_no_secret_material(listed.text)
    assert_no_secret_material(mutation.text)


def test_missing_and_cross_workspace_ids_have_identical_not_found_responses() -> None:
    service = RouteConnectionService()
    service.error = ConnectionNotFoundError()
    with authenticated_client(route_app(service)) as client:
        missing = client.post(
            f"/api/v1/firecrawl/connections/{MISSING_CONNECTION}/test",
            headers=csrf_headers(),
        )
        cross_workspace = client.post(
            f"/api/v1/firecrawl/connections/{CONNECTION_A}/test",
            headers=csrf_headers(),
        )

    expected = {
        "detail": {
            "code": "connection_not_found",
            "message": "Firecrawl connection not found.",
        }
    }
    assert missing.status_code == cross_workspace.status_code == 404
    assert missing.json() == cross_workspace.json() == expected


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (ConnectionNameConflictError(), 409, "connection_name_conflict"),
        (ConnectionDefaultConflictError(), 409, "connection_default_conflict"),
        (RemoteEndpointRejectedError(), 422, "connection_endpoint_rejected"),
        (ValueError("test-only unsafe detail"), 422, "connection_invalid"),
        (ConnectionUnavailableError(), 503, "connection_unavailable"),
        (CredentialUnavailableError(), 503, "credential_unavailable"),
    ],
)
def test_routes_map_domain_failures_to_stable_safe_errors(
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    service = RouteConnectionService()
    service.error = error
    with authenticated_client(route_app(service)) as client:
        response = client.post(
            f"/api/v1/firecrawl/connections/{CONNECTION_A}/test",
            headers=csrf_headers(),
        )

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    assert "test-only unsafe detail" not in response.text
    assert_no_secret_material(response.text)


def test_connection_routes_are_not_advertised_or_reachable_in_self_hosted_mode() -> None:
    service = RouteConnectionService()
    app = route_app(service, hosted=False)
    with TestClient(app, base_url="https://app.parserium.test") as client:
        response = client.get("/api/v1/firecrawl/connections")

    assert response.status_code == 404
    assert service.calls == []
