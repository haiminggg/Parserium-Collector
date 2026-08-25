from collections.abc import Awaitable, Callable

import httpx

from parserium_collector.features.health.models import ComponentHealth, ComponentStatus

HealthProbe = Callable[[], Awaitable[ComponentHealth]]


def firecrawl_health_probe(
    base_url: str | None,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HealthProbe:
    async def probe() -> ComponentHealth:
        if base_url is None:
            return ComponentHealth(
                name="firecrawl",
                status=ComponentStatus.NOT_CONFIGURED,
                detail="No Firecrawl endpoint is configured.",
                required=False,
            )
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=httpx.Timeout(timeout_seconds),
                transport=transport,
            ) as client:
                response = await client.get("/v0/health/liveness")
                response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            return ComponentHealth(
                name="firecrawl",
                status=ComponentStatus.UNAVAILABLE,
                detail="Firecrawl connection check failed.",
                required=False,
            )
        return ComponentHealth(
            name="firecrawl",
            status=ComponentStatus.DEGRADED,
            detail=(
                "Connected; release identity is operator-declared; metadata-only search is "
                "verified; fetch capabilities remain disabled."
            ),
            required=False,
        )

    return probe
