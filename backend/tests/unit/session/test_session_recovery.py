from datetime import UTC, datetime, timedelta

from parserium_collector.cli.session_recovery import run_recovery
from parserium_collector.features.session.models import IssuedPairingCode


class RecoveryService:
    async def recover(self, now: datetime) -> IssuedPairingCode:
        return IssuedPairingCode(
            code="replacement-pairing-code",
            expires_at=now + timedelta(minutes=10),
        )


async def test_recovery_prints_only_pairing_code_and_expiry() -> None:
    lines: list[str] = []
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)

    await run_recovery(RecoveryService(), now, lines.append)  # type: ignore[arg-type]

    output = "\n".join(lines)
    assert "replacement-pairing-code" in output
    assert "2026-08-24T12:10:00+00:00" in output
    assert "database" not in output.lower()
    assert "signing" not in output.lower()
    assert "digest" not in output.lower()
