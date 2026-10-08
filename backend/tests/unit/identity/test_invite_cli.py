from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from parserium_collector.cli import invite
from parserium_collector.features.identity.models import WorkspaceRole
from parserium_collector.features.session.crypto import keyed_digest
from parserium_collector.settings import Settings

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")
INVITATION_ID = UUID("20000000-0000-4000-8000-000000000002")
INVITATION_VALUE = "raw-invitation-token"


class RecordingRepository:
    def __init__(self, *, revoke_result: bool = True) -> None:
        self.created: list[dict[str, Any]] = []
        self.revoked: list[tuple[UUID, datetime]] = []
        self.revoke_result = revoke_result

    async def create_workspace_invitation(self, **values: Any) -> object:
        self.created.append(values)
        return object()

    async def revoke_invitation(self, invitation_id: UUID, now: datetime) -> bool:
        self.revoked.append((invitation_id, now))
        return self.revoke_result


@pytest.fixture
def cli_settings(tmp_path: Path) -> Settings:
    secret_file = tmp_path / "session-secret"
    secret_file.write_bytes(b"s" * 32)
    return Settings(
        public_origin="https://app.parserium.test",
        session_signing_secret_file=secret_file,
    )


@pytest.fixture
def fixed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(invite, "generate_session_token", lambda: INVITATION_VALUE)


async def _run(
    arguments: list[str],
    settings: Settings,
    repository: RecordingRepository,
) -> tuple[int, list[str]]:
    output: list[str] = []
    code = await invite.run_invitation_command(
        arguments,
        settings=settings,
        repository=repository,
        now=NOW,
        write_line=output.append,
    )
    return code, output


@pytest.mark.usefixtures("fixed_token")
@pytest.mark.asyncio
async def test_create_invitation_for_new_workspace_prints_one_url(
    cli_settings: Settings,
) -> None:
    repository = RecordingRepository()

    code, output = await _run(
        [
            "create",
            "--email",
            " Owner@Northbridge.Example ",
            "--workspace-name",
            "Northbridge",
            "--role",
            "owner",
            "--expires-hours",
            "24",
        ],
        cli_settings,
        repository,
    )

    assert code == 0
    assert output == [f"https://app.parserium.test/#invite={INVITATION_VALUE}"]
    assert len(repository.created) == 1
    created = repository.created[0]
    assert created["normalized_email"] == "owner@northbridge.example"
    assert created["workspace_name"] == "Northbridge"
    assert created["workspace_id"] is None
    assert created["role"] is WorkspaceRole.OWNER
    assert created["expires_at"] == NOW.replace(day=31, hour=12)
    assert created["token_digest"] == keyed_digest(
        cli_settings.session_signing_secret(), "invitation", INVITATION_VALUE
    )
    assert INVITATION_VALUE not in repr(repository.created)


@pytest.mark.usefixtures("fixed_token")
@pytest.mark.asyncio
async def test_create_invitation_for_existing_workspace_uses_identifier(
    cli_settings: Settings,
) -> None:
    repository = RecordingRepository()

    code, output = await _run(
        [
            "create",
            "--email",
            "analyst@northbridge.example",
            "--workspace-id",
            str(WORKSPACE_ID),
            "--role",
            "member",
            "--expires-hours",
            "24",
        ],
        cli_settings,
        repository,
    )

    assert code == 0
    assert output == [f"https://app.parserium.test/#invite={INVITATION_VALUE}"]
    assert repository.created[0]["workspace_name"] is None
    assert repository.created[0]["workspace_id"] == WORKSPACE_ID
    assert repository.created[0]["role"] is WorkspaceRole.MEMBER


@pytest.mark.asyncio
async def test_revoke_targets_only_the_requested_active_invitation(
    cli_settings: Settings,
) -> None:
    repository = RecordingRepository()

    code, output = await _run(
        ["revoke", "--invitation-id", str(INVITATION_ID)],
        cli_settings,
        repository,
    )

    assert code == 0
    assert output == ["Invitation revoked."]
    assert repository.revoked == [(INVITATION_ID, NOW)]


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "create",
            "--email",
            "owner@example.com",
            "--workspace-name",
            "Northbridge",
            "--workspace-id",
            str(WORKSPACE_ID),
            "--role",
            "owner",
            "--expires-hours",
            "24",
        ],
        [
            "create",
            "--email",
            "owner@example.com",
            "--workspace-name",
            "Northbridge",
            "--role",
            "member",
            "--expires-hours",
            "24",
        ],
    ],
)
@pytest.mark.asyncio
async def test_create_rejects_invalid_workspace_target_or_role(
    arguments: list[str],
    cli_settings: Settings,
) -> None:
    repository = RecordingRepository()
    output: list[str] = []

    code = await invite.run_invitation_command(
        arguments,
        settings=cli_settings,
        repository=repository,
        now=NOW,
        write_line=output.append,
    )

    assert code == 2
    assert repository.created == []
    assert len(output) == 1


@pytest.mark.asyncio
async def test_missing_invitation_returns_fixed_safe_error(cli_settings: Settings) -> None:
    repository = RecordingRepository(revoke_result=False)
    output: list[str] = []

    code = await invite.run_invitation_command(
        ["revoke", "--invitation-id", str(INVITATION_ID)],
        settings=cli_settings,
        repository=repository,
        now=NOW,
        write_line=output.append,
    )

    assert code == 1
    assert output == ["The invitation is invalid or unavailable."]
