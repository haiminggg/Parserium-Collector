from collections.abc import Awaitable, Callable

from parserium_collector.features.health.models import (
    ComponentHealth,
    ComponentStatus,
    HealthStatus,
)

Probe = Callable[[], Awaitable[ComponentHealth]]


class HealthService:
    def __init__(self, build_id: str, probes: tuple[Probe, ...]) -> None:
        self._build_id = build_id
        self._probes = probes

    async def status(self) -> HealthStatus:
        components = [await probe() for probe in self._probes]
        required = [component for component in components if component.required]
        is_ready = bool(required) and all(
            component.status is ComponentStatus.AVAILABLE for component in required
        )
        return HealthStatus(
            overall="ready" if is_ready else "degraded",
            build_id=self._build_id,
            components=components,
        )


def unavailable_health_service(build_id: str, detail: str) -> HealthService:
    async def unavailable_probe(name: str) -> ComponentHealth:
        return ComponentHealth(
            name=name,
            status=ComponentStatus.UNAVAILABLE,
            detail=detail,
        )

    async def database_probe() -> ComponentHealth:
        return await unavailable_probe("database")

    async def storage_probe() -> ComponentHealth:
        return await unavailable_probe("storage")

    async def worker_probe() -> ComponentHealth:
        return await unavailable_probe("worker")

    return HealthService(build_id, (database_probe, storage_probe, worker_probe))
