from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from parserium_collector.features.identity.models import (
    AuthenticationMode,
    WorkspaceRole,
    WorkspaceScope,
)
from parserium_collector.features.session.dependencies import (
    require_authenticated_session,
    require_csrf_session,
)
from parserium_collector.features.session.errors import InvalidSession
from parserium_collector.features.session.models import (
    AuthenticatedSession,
    WorkspaceSummary,
)
from parserium_collector.settings import DeploymentMode, Settings

TEST_SESSION_VALUE = "valid-session"
TEST_CSRF_VALUE = "valid-csrf"
LOCAL_WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")


class DependencySessionService:
    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        if token != TEST_SESSION_VALUE:
            raise InvalidSession
        return AuthenticatedSession(
            token_digest="a" * 64,
            csrf_token=TEST_CSRF_VALUE,
            idle_expires_at=now + timedelta(hours=24),
            authentication_mode=AuthenticationMode.LOCAL,
            scope=WorkspaceScope(
                workspace_id=LOCAL_WORKSPACE_ID,
                user_id=None,
                role=WorkspaceRole.OWNER,
            ),
            workspace_name="Local workspace",
            email=None,
            display_name=None,
            workspaces=(
                WorkspaceSummary(
                    id=LOCAL_WORKSPACE_ID,
                    name="Local workspace",
                    role=WorkspaceRole.OWNER,
                ),
            ),
        )


def protected_app() -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings()
    app.state.session_service = DependencySessionService()

    @app.get("/protected", dependencies=[Depends(require_authenticated_session)])
    async def protected_read() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/protected", dependencies=[Depends(require_csrf_session)])
    async def protected_write() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_authenticated_dependency_requires_valid_cookie() -> None:
    with TestClient(protected_app()) as client:
        missing = client.get("/protected")
        client.cookies.set("parserium_session", "invalid")
        invalid = client.get("/protected")
        client.cookies.set("parserium_session", TEST_SESSION_VALUE)
        valid = client.get("/protected")

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200


def test_csrf_dependency_requires_matching_header() -> None:
    with TestClient(protected_app()) as client:
        client.cookies.set("parserium_session", TEST_SESSION_VALUE)
        missing = client.post("/protected")
        invalid = client.post("/protected", headers={"X-Parserium-CSRF": "wrong"})
        valid = client.post("/protected", headers={"X-Parserium-CSRF": TEST_CSRF_VALUE})

    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert valid.status_code == 200


def test_authenticated_dependency_uses_hosted_identity_service(tmp_path: Path) -> None:
    session_secret = tmp_path / "session-secret"
    session_secret.write_bytes(b"s" * 32)
    oidc_secret = tmp_path / "oidc-secret"
    oidc_secret.write_bytes(b"o" * 32)
    app = FastAPI()
    app.state.settings = Settings(
        deployment_mode=DeploymentMode.HOSTED,
        storage_backend="s3",
        storage_endpoint="https://minio.parserium.test",
        storage_region="us-east-1",
        storage_bucket="parserium-artifacts",
        public_origin="https://app.parserium.test",
        session_cookie_secure=True,
        session_signing_secret_file=session_secret,
        oidc_issuer="https://identity.parserium.test",
        oidc_client_id="parserium-client",
        oidc_client_secret_file=oidc_secret,
        oidc_redirect_uri="https://app.parserium.test/api/v1/auth/callback",
    )
    app.state.identity_service = DependencySessionService()

    @app.get("/hosted", dependencies=[Depends(require_authenticated_session)])
    async def hosted_read() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(app, base_url="https://app.parserium.test") as client:
        client.cookies.set("parserium_session", TEST_SESSION_VALUE)
        response = client.get("/hosted")

    assert response.status_code == 200
