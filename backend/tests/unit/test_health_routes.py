from fastapi.testclient import TestClient

from parserium_collector.features.health.models import ComponentHealth, ComponentStatus
from parserium_collector.features.health.service import HealthService
from parserium_collector.main import create_app
from parserium_collector.settings import Settings


def health_service(status: ComponentStatus) -> HealthService:
    async def database_probe() -> ComponentHealth:
        return ComponentHealth(name="database", status=status)

    return HealthService("test-build", (database_probe,))


def test_status_returns_component_health() -> None:
    app = create_app(
        Settings(build_id="test-build"),
        health_service=health_service(ComponentStatus.AVAILABLE),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/health/status")

    assert response.status_code == 200
    assert response.json() == {
        "overall": "ready",
        "build_id": "test-build",
        "components": [
            {
                "name": "database",
                "status": "available",
                "detail": None,
                "required": True,
            }
        ],
    }


def test_readiness_returns_service_unavailable_when_degraded() -> None:
    app = create_app(
        Settings(build_id="test-build"),
        health_service=health_service(ComponentStatus.UNAVAILABLE),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["overall"] == "degraded"
