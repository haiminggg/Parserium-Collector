from fastapi import FastAPI
from fastapi.testclient import TestClient

from parserium_collector.security import LocalRequestGuardMiddleware


def guarded_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        LocalRequestGuardMiddleware,
        allowed_hosts=("127.0.0.1", "localhost"),
        allowed_origins=("http://127.0.0.1:8080", "http://localhost:8080"),
    )

    @app.get("/api/test")
    async def read_test() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/test")
    async def write_test() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_request_guard_accepts_configured_hosts() -> None:
    with TestClient(guarded_app(), base_url="http://127.0.0.1:8080") as client:
        loopback = client.get("/api/test")
    with TestClient(guarded_app(), base_url="http://localhost:8080") as client:
        localhost = client.get("/api/test")

    assert loopback.status_code == 200
    assert localhost.status_code == 200


def test_request_guard_rejects_unconfigured_host() -> None:
    with TestClient(guarded_app(), base_url="http://attacker.invalid") as client:
        response = client.get("/api/test")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid host."}


def test_request_guard_requires_allowed_origin_for_unsafe_api_method() -> None:
    with TestClient(guarded_app(), base_url="http://127.0.0.1:8080") as client:
        missing = client.post("/api/test")
        hostile = client.post("/api/test", headers={"Origin": "https://attacker.invalid"})
        allowed = client.post(
            "/api/test",
            headers={"Origin": "http://127.0.0.1:8080"},
        )

    assert missing.status_code == 403
    assert hostile.status_code == 403
    assert missing.json() == {"detail": "Invalid origin."}
    assert allowed.status_code == 200
