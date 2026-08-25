import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from parserium_collector.features.health.service import unavailable_health_service
from parserium_collector.features.session.models import IssuedPairingCode
from parserium_collector.features.session.service import SessionService
from parserium_collector.main import create_app
from parserium_collector.settings import Settings


def test_app_wires_session_routes_guards_and_one_startup_pairing_code(caplog: object) -> None:
    session_service = AsyncMock(spec=SessionService)
    session_service.ensure_pairing_code.return_value = IssuedPairingCode(
        code="local-pairing-code",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    app = create_app(
        Settings(static_root="/missing"),
        health_service=unavailable_health_service("test", "test"),
        session_service=session_service,
    )

    with caplog.at_level(logging.WARNING):  # type: ignore[attr-defined]
        with TestClient(app, base_url="http://127.0.0.1:8080") as client:
            status = client.get("/api/v1/session/status")
            hostile = client.get(
                "/api/v1/health/live",
                headers={"Host": "attacker.invalid"},
            )

    assert status.status_code == 200
    assert status.json() == {"status": "pairing_required"}
    assert hostile.status_code == 400
    assert "/api/v1/discovery/search" in app.openapi()["paths"]
    session_service.ensure_pairing_code.assert_awaited_once()
    assert "local-pairing-code" in caplog.text  # type: ignore[attr-defined]
