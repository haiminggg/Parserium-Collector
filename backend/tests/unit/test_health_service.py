from parserium_collector.features.health.models import ComponentHealth, ComponentStatus
from parserium_collector.features.health.service import HealthService, unavailable_health_service


async def test_readiness_is_ready_only_when_every_component_is_available() -> None:
    async def database_probe() -> ComponentHealth:
        return ComponentHealth(name="database", status=ComponentStatus.AVAILABLE)

    async def storage_probe() -> ComponentHealth:
        return ComponentHealth(name="storage", status=ComponentStatus.AVAILABLE)

    service = HealthService("test-build", (database_probe, storage_probe))

    result = await service.status()

    assert result.overall == "ready"
    assert [component.name for component in result.components] == ["database", "storage"]


async def test_readiness_is_degraded_when_one_component_is_unavailable() -> None:
    async def database_probe() -> ComponentHealth:
        return ComponentHealth(name="database", status=ComponentStatus.AVAILABLE)

    async def worker_probe() -> ComponentHealth:
        return ComponentHealth(
            name="worker",
            status=ComponentStatus.UNAVAILABLE,
            detail="No fresh heartbeat.",
        )

    service = HealthService("test-build", (database_probe, worker_probe))

    result = await service.status()

    assert result.overall == "degraded"
    assert result.components[1].detail == "No fresh heartbeat."


async def test_optional_component_does_not_block_readiness() -> None:
    async def database_probe() -> ComponentHealth:
        return ComponentHealth(name="database", status=ComponentStatus.AVAILABLE)

    async def firecrawl_probe() -> ComponentHealth:
        return ComponentHealth(
            name="firecrawl",
            status=ComponentStatus.NOT_CONFIGURED,
            required=False,
        )

    service = HealthService("test-build", (database_probe, firecrawl_probe))

    result = await service.status()

    assert result.overall == "ready"


async def test_unavailable_service_marks_required_components_unavailable() -> None:
    service = unavailable_health_service("test-build", "Installation settings are unavailable.")

    result = await service.status()

    assert result.overall == "degraded"
    assert [component.name for component in result.components] == [
        "database",
        "storage",
        "worker",
    ]
    assert all(component.status is ComponentStatus.UNAVAILABLE for component in result.components)
    assert all(
        component.detail == "Installation settings are unavailable."
        for component in result.components
    )
