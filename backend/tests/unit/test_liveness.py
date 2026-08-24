from fastapi.testclient import TestClient

from parserium_collector.main import create_app
from parserium_collector.settings import Settings


def test_liveness_returns_build_identity_without_dependency_calls() -> None:
    app = create_app(
        Settings(
            build_id="test-build",
            release_version="0.1.0-test",
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "alive",
        "build_id": "test-build",
        "release_version": "0.1.0-test",
    }
