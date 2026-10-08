import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

from parserium_collector.adapters.database.engine import create_engine
from parserium_collector.features.firecrawl_connections.crypto import (
    FileKeyProvider,
    rewrap_credential_envelope,
)
from parserium_collector.features.firecrawl_connections.errors import (
    CredentialUnavailableError,
)
from parserium_collector.features.firecrawl_connections.models import (
    FirecrawlConnectionRecord,
    RewrappedEnvelope,
)
from parserium_collector.features.firecrawl_connections.repository import (
    PostgresFirecrawlConnectionRepository,
)
from parserium_collector.settings import Settings


class RewrapRepository(Protocol):
    async def list_envelopes_for_rewrap(
        self,
        after_id: UUID | None,
        batch_size: int,
    ) -> tuple[FirecrawlConnectionRecord, ...]: ...

    async def replace_wrapped_keys(
        self,
        replacements: tuple[RewrappedEnvelope, ...],
    ) -> int: ...


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m parserium_collector.cli.rewrap_firecrawl_credentials"
    )
    parser.add_argument("--old-key-file", required=True, type=Path)
    parser.add_argument("--old-key-id", required=True)
    parser.add_argument("--new-key-file", required=True, type=Path)
    parser.add_argument("--new-key-id", required=True)
    parser.add_argument("--batch-size", required=True, type=int)
    return parser


async def run_rewrap_command(
    arguments: Sequence[str],
    *,
    repository: RewrapRepository,
    write_line: Callable[[str], None] = print,
) -> int:
    try:
        parsed = _parser().parse_args(arguments)
    except SystemExit:
        write_line("The Firecrawl credential rewrap request is invalid.")
        return 2
    if parsed.old_key_id == parsed.new_key_id or not 1 <= parsed.batch_size <= 1000:
        write_line("The Firecrawl credential rewrap request is invalid.")
        return 2
    try:
        old_provider = FileKeyProvider.from_file(
            key_id=parsed.old_key_id,
            path=parsed.old_key_file,
        )
        new_provider = FileKeyProvider.from_file(
            key_id=parsed.new_key_id,
            path=parsed.new_key_file,
        )
        count = 0
        after_id: UUID | None = None
        while True:
            records = await repository.list_envelopes_for_rewrap(
                after_id,
                parsed.batch_size,
            )
            if not records:
                break
            replacements: list[RewrappedEnvelope] = []
            for record in records:
                if record.credential_envelope is None:
                    raise CredentialUnavailableError
                replacements.append(
                    RewrappedEnvelope(
                        workspace_id=record.workspace_id,
                        connection_id=record.id,
                        expected_key_id=old_provider.key_id,
                        envelope=rewrap_credential_envelope(
                            record.credential_envelope,
                            old_provider,
                            new_provider,
                        ),
                    )
                )
            replaced = await repository.replace_wrapped_keys(tuple(replacements))
            if replaced != len(replacements):
                raise CredentialUnavailableError
            count += replaced
            after_id = records[-1].id
    except (CredentialUnavailableError, ValueError):
        write_line("Firecrawl credential rewrap failed safely.")
        return 1
    write_line(f"Rewrapped {count} Firecrawl credential envelopes.")
    return 0


async def _main(arguments: Sequence[str]) -> int:
    settings = Settings()
    engine = create_engine(settings)
    try:
        return await run_rewrap_command(
            arguments,
            repository=PostgresFirecrawlConnectionRepository(engine),
        )
    finally:
        await engine.dispose()


def main() -> None:
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
