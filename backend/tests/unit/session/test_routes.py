from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from parserium_collector.features.session.errors import (
    InvalidCsrfToken,
    InvalidPairingCode,
    InvalidSession,
)
from parserium_collector.features.session.models import AuthenticatedSession, IssuedSession
from parserium_collector.features.session.router import router
from parserium_collector.security import LocalRequestGuardMiddleware
from parserium_collector.settings import Settings

TEST_PAIRING_CODE = "valid-pairing-code"
TEST_SESSION_VALUE = "session-token"
TEST_CSRF_VALUE = "csrf-token"


class RouteSessionService:
    def __init__(self) -> None:
        self.revoked = False

    async def pair(self, code: str, now: datetime) -> IssuedSession:
        if code != TEST_PAIRING_CODE:
            raise InvalidPairingCode
        return IssuedSession(
            token=TEST_SESSION_VALUE,
            csrf_token=TEST_CSRF_VALUE,
            idle_expires_at=now + timedelta(hours=24),
        )

    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        if token != TEST_SESSION_VALUE or self.revoked:
            raise InvalidSession
        return AuthenticatedSession(
            csrf_token=TEST_CSRF_VALUE,
            idle_expires_at=now + timedelta(hours=24),
        )

    async def logout(self, token: str, supplied_csrf_token: str, now: datetime) -> None:
        await self.authenticate(token, now)
        if supplied_csrf_token != TEST_CSRF_VALUE:
            raise InvalidCsrfToken
        self.revoked = True


def session_app() -> tuple[FastAPI, RouteSessionService]:
    service = RouteSessionService()
    app = FastAPI()
    app.state.settings = Settings(
        allowed_hosts=("127.0.0.1", "localhost"),
        allowed_origins=("http://127.0.0.1:8080", "http://localhost:8080"),
        session_cookie_secure=False,
        session_idle_seconds=86400,
    )
    app.state.session_service = service
    app.add_middleware(
        LocalRequestGuardMiddleware,
        allowed_hosts=app.state.settings.allowed_hosts,
        allowed_origins=app.state.settings.allowed_origins,
    )
    app.include_router(router)
    return app, service


def test_status_requires_pairing_without_valid_cookie() -> None:
    app, _ = session_app()
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        response = client.get("/api/v1/session/status")

    assert response.status_code == 200
    assert response.json() == {"status": "pairing_required"}


def test_pairing_uses_generic_errors_and_sets_restricted_cookie() -> None:
    app, _ = session_app()
    origin = {"Origin": "http://127.0.0.1:8080"}
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        invalid = client.post(
            "/api/v1/session/pair",
            json={"code": "wrong"},
            headers=origin,
        )
        paired = client.post(
            "/api/v1/session/pair",
            json={"code": TEST_PAIRING_CODE},
            headers=origin,
        )

    assert invalid.status_code == 401
    assert invalid.json() == {"detail": "Invalid or expired pairing code."}
    assert paired.status_code == 200
    assert paired.json()["status"] == "authenticated"
    assert paired.json()["csrf_token"] == TEST_CSRF_VALUE
    cookie = paired.headers["set-cookie"]
    assert "parserium_session=session-token" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Max-Age=86400" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie


def test_authenticated_session_requires_csrf_for_logout_and_revokes_cookie() -> None:
    app, service = session_app()
    origin = {"Origin": "http://127.0.0.1:8080"}
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        client.cookies.set("parserium_session", TEST_SESSION_VALUE)
        current = client.get("/api/v1/session")
        rejected = client.post(
            "/api/v1/session/logout",
            headers={**origin, "X-Parserium-CSRF": "wrong"},
        )
        logout = client.post(
            "/api/v1/session/logout",
            headers={**origin, "X-Parserium-CSRF": TEST_CSRF_VALUE},
        )

    assert current.status_code == 200
    assert current.json()["csrf_token"] == TEST_CSRF_VALUE
    assert rejected.status_code == 403
    assert service.revoked is True
    assert logout.status_code == 204
    assert "parserium_session=\"\"" in logout.headers["set-cookie"]
    assert "Max-Age=0" in logout.headers["set-cookie"]
