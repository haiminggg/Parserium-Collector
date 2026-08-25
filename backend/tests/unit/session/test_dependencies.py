from datetime import datetime, timedelta

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from parserium_collector.features.session.dependencies import (
    require_authenticated_session,
    require_csrf_session,
)
from parserium_collector.features.session.errors import InvalidSession
from parserium_collector.features.session.models import AuthenticatedSession

TEST_SESSION_VALUE = "valid-session"
TEST_CSRF_VALUE = "valid-csrf"


class DependencySessionService:
    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        if token != TEST_SESSION_VALUE:
            raise InvalidSession
        return AuthenticatedSession(
            csrf_token=TEST_CSRF_VALUE,
            idle_expires_at=now + timedelta(hours=24),
        )


def protected_app() -> FastAPI:
    app = FastAPI()
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
