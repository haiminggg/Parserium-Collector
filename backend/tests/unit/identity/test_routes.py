from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

from parserium_collector.features.identity.models import (
    AuthenticationMode,
    HostedSessionNotFound,
    OidcClaims,
    WorkspaceMembershipNotFound,
    WorkspaceRole,
    WorkspaceScope,
)
from parserium_collector.features.identity.router import router as identity_router
from parserium_collector.features.session.crypto import keyed_digest
from parserium_collector.features.session.models import (
    AuthenticatedSession,
    IssuedSession,
    WorkspaceSummary,
)
from parserium_collector.features.session.router import router as session_router
from parserium_collector.security import LocalRequestGuardMiddleware
from parserium_collector.settings import DeploymentMode, Settings

SESSION_VALUE = "hosted-session-value"
CSRF_VALUE = "hosted-csrf-value"
INVITE_VALUE = "A" * 43
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")
USER_ID = UUID("20000000-0000-4000-8000-000000000001")
SECOND_WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000002")
CLAIMS = OidcClaims(
    issuer="https://identity.parserium.test",
    subject="subject-123",
    email="owner@parserium.test",
    email_verified=True,
    display_name="Parserium Owner",
)


class RouteOidcAdapter:
    def __init__(self) -> None:
        self.login_sessions: list[dict[str, Any]] = []
        self.callback_calls = 0

    async def authorize_redirect(
        self,
        request: Request,
        redirect_uri: str,
    ) -> RedirectResponse:
        self.login_sessions.append(dict(request.session))
        return RedirectResponse(
            f"https://identity.parserium.test/authorize?redirect_uri={redirect_uri}",
            status_code=302,
        )

    async def authorize_access_token(self, request: Request) -> OidcClaims:
        self.callback_calls += 1
        return CLAIMS


class RouteIdentityService:
    def __init__(self) -> None:
        self.invitation_digests: list[str | None] = []

    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        if token != SESSION_VALUE:
            raise HostedSessionNotFound
        return self._authenticated(now, WORKSPACE_ID, "Northbridge")

    async def switch_workspace(
        self,
        token: str,
        workspace_id: UUID,
        now: datetime,
    ) -> AuthenticatedSession:
        if workspace_id != SECOND_WORKSPACE_ID:
            raise WorkspaceMembershipNotFound
        return self._authenticated(now, SECOND_WORKSPACE_ID, "Research")

    def _authenticated(
        self,
        now: datetime,
        workspace_id: UUID,
        workspace_name: str,
    ) -> AuthenticatedSession:
        return AuthenticatedSession(
            token_digest="d" * 64,
            csrf_token=CSRF_VALUE,
            idle_expires_at=now + timedelta(hours=24),
            authentication_mode=AuthenticationMode.OIDC,
            scope=WorkspaceScope(
                workspace_id=workspace_id,
                user_id=USER_ID,
                role=WorkspaceRole.OWNER,
            ),
            workspace_name=workspace_name,
            email=CLAIMS.email,
            display_name=CLAIMS.display_name,
            workspaces=(
                WorkspaceSummary(
                    id=WORKSPACE_ID,
                    name="Northbridge",
                    role=WorkspaceRole.OWNER,
                ),
                WorkspaceSummary(
                    id=SECOND_WORKSPACE_ID,
                    name="Research",
                    role=WorkspaceRole.MEMBER,
                ),
            ),
        )

    async def complete_login(
        self,
        claims: OidcClaims,
        *,
        invitation_digest: str | None,
        now: datetime,
    ) -> IssuedSession:
        self.invitation_digests.append(invitation_digest)
        workspace = WorkspaceSummary(
            id=WORKSPACE_ID,
            name="Northbridge",
            role=WorkspaceRole.OWNER,
        )
        return IssuedSession(
            token=SESSION_VALUE,
            csrf_token=CSRF_VALUE,
            idle_expires_at=now + timedelta(hours=24),
            authentication_mode=AuthenticationMode.OIDC,
            scope=WorkspaceScope(
                workspace_id=WORKSPACE_ID,
                user_id=USER_ID,
                role=WorkspaceRole.OWNER,
            ),
            workspace_name="Northbridge",
            email=claims.email,
            display_name=claims.display_name,
            workspaces=(workspace,),
        )


def _hosted_settings(tmp_path: Path) -> Settings:
    session_secret = tmp_path / "session-secret"
    session_secret.write_bytes(b"s" * 32)
    oidc_secret = tmp_path / "oidc-secret"
    oidc_secret.write_bytes(b"o" * 32)
    return Settings(
        deployment_mode=DeploymentMode.HOSTED,
        storage_backend="s3",
        storage_endpoint="https://minio.parserium.test",
        storage_region="us-east-1",
        storage_bucket="parserium-artifacts",
        public_origin="https://app.parserium.test",
        allowed_hosts=("app.parserium.test",),
        allowed_origins=("https://app.parserium.test",),
        session_cookie_secure=True,
        session_signing_secret_file=session_secret,
        oidc_issuer="https://identity.parserium.test",
        oidc_client_id="parserium-client",
        oidc_client_secret_file=oidc_secret,
        oidc_redirect_uri="https://app.parserium.test/api/v1/auth/callback",
    )


def hosted_app(
    tmp_path: Path,
) -> tuple[FastAPI, RouteOidcAdapter, RouteIdentityService, Settings]:
    settings = _hosted_settings(tmp_path)
    oidc = RouteOidcAdapter()
    identity = RouteIdentityService()
    app = FastAPI()
    app.state.settings = settings
    app.state.oidc_adapter = oidc
    app.state.identity_service = identity
    app.add_middleware(
        LocalRequestGuardMiddleware,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=keyed_digest(
            settings.session_signing_secret(),
            "oidc-flow-cookie",
            "v1",
        ),
        session_cookie="parserium_oidc_flow",
        max_age=settings.oidc_flow_ttl_seconds,
        same_site="lax",
        https_only=True,
    )
    app.include_router(identity_router)
    app.include_router(session_router)
    return app, oidc, identity, settings


def test_hosted_status_returns_login_required_without_cookie(tmp_path: Path) -> None:
    app, _, _, _ = hosted_app(tmp_path)
    with TestClient(app, base_url="https://app.parserium.test") as client:
        response = client.get("/api/v1/session/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "login_required",
        "login_url": "/api/v1/auth/login",
        "provider_label": "Identity provider",
    }


def test_callback_sets_restricted_cookie_and_clears_flow_cookie(tmp_path: Path) -> None:
    app, oidc, identity, settings = hosted_app(tmp_path)
    origin = {"Origin": "https://app.parserium.test"}
    with TestClient(app, base_url="https://app.parserium.test") as client:
        login = client.post(
            "/api/v1/auth/login",
            data={"invite": INVITE_VALUE},
            headers=origin,
            follow_redirects=False,
        )
        callback = client.get(
            "/api/v1/auth/callback?code=test-code&state=test-state",
            follow_redirects=False,
        )

    assert login.status_code == 302
    assert len(oidc.login_sessions) == 1
    flow = oidc.login_sessions[0]
    assert set(flow) == {"invitation_digest"}
    assert len(flow["invitation_digest"]) == 64
    assert flow["invitation_digest"] == keyed_digest(
        settings.session_signing_secret(), "invitation", INVITE_VALUE
    )
    assert INVITE_VALUE not in repr(flow)
    assert identity.invitation_digests == [flow["invitation_digest"]]
    assert callback.status_code == 303
    cookies = callback.headers.get_list("set-cookie")
    session_cookie = next(value for value in cookies if "parserium_session=" in value)
    assert "Secure" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert any(
        "parserium_oidc_flow=null" in value and "expires=Thu, 01 Jan 1970 00:00:00 GMT" in value
        for value in cookies
    )


def test_hosted_pair_route_is_not_available(tmp_path: Path) -> None:
    app, _, _, _ = hosted_app(tmp_path)
    with TestClient(app, base_url="https://app.parserium.test") as client:
        response = client.post(
            "/api/v1/session/pair",
            json={"code": "not-used"},
            headers={"Origin": "https://app.parserium.test"},
        )
    assert response.status_code == 404


def test_login_origin_is_checked_before_form_processing(tmp_path: Path) -> None:
    app, oidc, _, _ = hosted_app(tmp_path)
    with TestClient(app, base_url="https://app.parserium.test") as client:
        missing = client.post("/api/v1/auth/login", data={"invite": INVITE_VALUE})
        mismatched = client.post(
            "/api/v1/auth/login",
            data={"invite": INVITE_VALUE},
            headers={"Origin": "https://attacker.test"},
        )
    assert missing.status_code == 403
    assert mismatched.status_code == 403
    assert oidc.login_sessions == []


def test_malformed_invitation_does_not_start_oidc(tmp_path: Path) -> None:
    app, oidc, _, _ = hosted_app(tmp_path)
    with TestClient(app, base_url="https://app.parserium.test") as client:
        response = client.post(
            "/api/v1/auth/login",
            data={"invite": "not-a-token"},
            headers={"Origin": "https://app.parserium.test"},
        )
    assert response.status_code == 422
    assert oidc.login_sessions == []


def test_workspace_switch_requires_csrf_and_membership(tmp_path: Path) -> None:
    app, _, _, _ = hosted_app(tmp_path)
    origin = {"Origin": "https://app.parserium.test"}
    with TestClient(app, base_url="https://app.parserium.test") as client:
        client.cookies.set("parserium_session", SESSION_VALUE)
        missing_csrf = client.post(
            "/api/v1/session/workspace",
            json={"workspace_id": str(SECOND_WORKSPACE_ID)},
            headers=origin,
        )
        nonmember = client.post(
            "/api/v1/session/workspace",
            json={"workspace_id": "10000000-0000-4000-8000-000000000099"},
            headers={**origin, "X-Parserium-CSRF": CSRF_VALUE},
        )
        switched = client.post(
            "/api/v1/session/workspace",
            json={"workspace_id": str(SECOND_WORKSPACE_ID)},
            headers={**origin, "X-Parserium-CSRF": CSRF_VALUE},
        )

    assert missing_csrf.status_code == 403
    assert nonmember.status_code == 404
    assert switched.status_code == 200
    assert switched.json()["workspace"]["id"] == str(SECOND_WORKSPACE_ID)
    assert switched.json()["authentication_mode"] == "oidc"
