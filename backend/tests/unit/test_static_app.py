from pathlib import Path

from fastapi.testclient import TestClient

from parserium_collector.features.health.service import unavailable_health_service
from parserium_collector.main import create_app
from parserium_collector.settings import Settings


def test_static_index_is_served_without_intercepting_api_routes(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>Parserium Collector</title>",
        encoding="utf-8",
    )
    settings = Settings(static_root=tmp_path, build_id="static-test")
    app = create_app(
        settings,
        health_service=unavailable_health_service("static-test", "test"),
    )

    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        root = client.get("/")
        live = client.get("/api/v1/health/live")

    assert root.status_code == 200
    assert "Parserium Collector" in root.text
    assert live.status_code == 200
