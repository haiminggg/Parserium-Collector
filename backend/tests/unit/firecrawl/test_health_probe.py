import httpx

from parserium_collector.adapters.firecrawl.health import firecrawl_health_probe
from parserium_collector.features.health.models import ComponentStatus


async def test_unconfigured_firecrawl_is_optional_and_not_configured() -> None:
    probe = firecrawl_health_probe(None, 2.0)

    result = await probe()

    assert result.name == "firecrawl"
    assert result.required is False
    assert result.status is ComponentStatus.NOT_CONFIGURED


async def test_connected_firecrawl_reports_verified_metadata_only_capability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    probe = firecrawl_health_probe(
        "http://firecrawl.test",
        2.0,
        transport=httpx.MockTransport(handler),
    )

    result = await probe()

    assert result.status is ComponentStatus.DEGRADED
    assert result.required is False
    assert "operator-declared" in (result.detail or "")
    assert "metadata-only search is verified" in (result.detail or "")
    assert "fetch capabilities remain disabled" in (result.detail or "")
