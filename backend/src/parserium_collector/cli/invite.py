import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, ValidationError

from parserium_collector.adapters.database.engine import create_engine
from parserium_collector.features.identity.models import (
    InvitationRejected,
    WorkspaceMembershipNotFound,
    WorkspaceRole,
)
from parserium_collector.features.identity.repository import (
    IdentityRepository,
    PostgresIdentityRepository,
)
from parserium_collector.features.session.crypto import (
    generate_session_token,
    keyed_digest,
)
from parserium_collector.settings import Settings


class _InvitationEmail(BaseModel):
    email: EmailStr


class _WorkspaceName(BaseModel):
    value: str = Field(min_length=1, max_length=120)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m parserium_collector.cli.invite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--email", required=True)
    create.add_argument("--workspace-name")
    create.add_argument("--workspace-id", type=UUID)
    create.add_argument("--role", required=True, choices=[role.value for role in WorkspaceRole])
    create.add_argument("--expires-hours", required=True, type=int)

    revoke = subparsers.add_parser("revoke")
    revoke.add_argument("--invitation-id", required=True, type=UUID)
    return parser


async def run_invitation_command(
    arguments: Sequence[str],
    *,
    settings: Settings,
    repository: IdentityRepository,
    now: datetime,
    write_line: Callable[[str], None] = print,
) -> int:
    try:
        parsed = _parser().parse_args(arguments)
    except SystemExit:
        write_line("Invalid invitation command.")
        return 2

    if parsed.command == "revoke":
        revoked = await repository.revoke_invitation(parsed.invitation_id, now)
        if not revoked:
            write_line(str(InvitationRejected()))
            return 1
        write_line("Invitation revoked.")
        return 0

    workspace_name = parsed.workspace_name
    workspace_id = parsed.workspace_id
    if (workspace_name is None) == (workspace_id is None):
        write_line("Choose exactly one workspace target.")
        return 2

    role = WorkspaceRole(parsed.role)
    if workspace_name is not None and role is not WorkspaceRole.OWNER:
        write_line("A new workspace invitation must grant owner role.")
        return 2
    if not 1 <= parsed.expires_hours <= 168:
        write_line("Invitation expiry must be between 1 and 168 hours.")
        return 2

    try:
        normalized_email = str(_InvitationEmail(email=parsed.email).email).casefold()
        if workspace_name is not None:
            workspace_name = _WorkspaceName(value=workspace_name.strip()).value
    except ValidationError:
        write_line("The invitation details are invalid.")
        return 2

    raw_token = generate_session_token()
    token_digest = keyed_digest(
        settings.session_signing_secret(),
        "invitation",
        raw_token,
    )
    try:
        await repository.create_workspace_invitation(
            workspace_name=workspace_name,
            workspace_id=workspace_id,
            normalized_email=normalized_email,
            token_digest=token_digest,
            role=role,
            created_at=now,
            expires_at=now + timedelta(hours=parsed.expires_hours),
        )
    except WorkspaceMembershipNotFound as error:
        write_line(str(error))
        return 1

    invite_url = f"{str(settings.public_origin).rstrip('/')}/#invite={quote(raw_token)}"
    write_line(invite_url)
    return 0


async def _main(arguments: Sequence[str]) -> int:
    settings = Settings()
    engine = create_engine(settings)
    try:
        return await run_invitation_command(
            arguments,
            settings=settings,
            repository=PostgresIdentityRepository(engine),
            now=datetime.now(UTC),
        )
    finally:
        await engine.dispose()


def main() -> None:
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
