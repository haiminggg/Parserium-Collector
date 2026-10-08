import base64
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from parserium_collector.cli.rewrap_firecrawl_credentials import run_rewrap_command
from parserium_collector.features.firecrawl_connections.crypto import (
    CredentialVault,
    FileKeyProvider,
)
from parserium_collector.features.firecrawl_connections.errors import (
    CredentialUnavailableError,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionType,
    FirecrawlConnectionRecord,
    RewrappedEnvelope,
)

NOW = datetime(2026, 9, 2, 16, 0, tzinfo=UTC)
WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000019")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000029")
CONNECTION_A = UUID("30000000-0000-4000-8000-000000000019")
CONNECTION_B = UUID("30000000-0000-4000-8000-000000000029")


def key_file(path: Path, key: bytes) -> Path:
    path.write_bytes(base64.b64encode(key))
    return path


async def record(
    workspace_id: UUID,
    connection_id: UUID,
    credential: str,
    provider: FileKeyProvider,
) -> FirecrawlConnectionRecord:
    envelope = await CredentialVault(provider).encrypt(
        credential,
        workspace_id,
        connection_id,
        1,
    )
    return FirecrawlConnectionRecord(
        id=connection_id,
        workspace_id=workspace_id,
        name=f"Connection {connection_id}",
        normalized_name=str(connection_id),
        connection_type=ConnectionType.CLOUD,
        normalized_base_url=None,
        credential_envelope=envelope,
        credential_revision=1,
        validated_revision=1,
        validation_succeeded=True,
        capability_profile={"contract": "metadata-search-v2"},
        last_validation_attempt_at=NOW,
        last_validation_success_at=NOW,
        last_failure_category=None,
        enabled=True,
        is_default=False,
        created_by_user_id=None,
        updated_by_user_id=None,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
    )


class RewrapRepositoryTestDouble:
    """In-memory persistence test double for the command boundary."""

    def __init__(self, records: tuple[FirecrawlConnectionRecord, ...]) -> None:
        self.records = {(item.workspace_id, item.id): item for item in records}
        self.batch_sizes: list[int] = []

    async def list_envelopes_for_rewrap(
        self,
        after_id: UUID | None,
        batch_size: int,
    ) -> tuple[FirecrawlConnectionRecord, ...]:
        self.batch_sizes.append(batch_size)
        ordered = sorted(self.records.values(), key=lambda item: item.id)
        if after_id is not None:
            ordered = [item for item in ordered if item.id > after_id]
        return tuple(ordered[:batch_size])

    async def replace_wrapped_keys(
        self,
        replacements: tuple[RewrappedEnvelope, ...],
    ) -> int:
        for replacement in replacements:
            key = (replacement.workspace_id, replacement.connection_id)
            current = self.records[key]
            assert current.credential_envelope is not None
            if current.credential_envelope.key_id != replacement.expected_key_id:
                raise CredentialUnavailableError
            self.records[key] = replace(current, credential_envelope=replacement.envelope)
        return len(replacements)


async def test_rewrap_changes_only_wrapped_keys_across_bounded_batches(tmp_path: Path) -> None:
    old_provider = FileKeyProvider.from_bytes(key_id="old-v1", key=b"o" * 32)
    new_provider = FileKeyProvider.from_bytes(key_id="new-v2", key=b"n" * 32)
    records = (
        await record(WORKSPACE_A, CONNECTION_A, "workspace-a-test-only-token", old_provider),
        await record(WORKSPACE_B, CONNECTION_B, "workspace-b-test-only-token", old_provider),
    )
    original = {(item.workspace_id, item.id): item.credential_envelope for item in records}
    repository = RewrapRepositoryTestDouble(records)
    output: list[str] = []

    code = await run_rewrap_command(
        [
            "--old-key-file",
            str(key_file(tmp_path / "old-key", b"o" * 32)),
            "--old-key-id",
            "old-v1",
            "--new-key-file",
            str(key_file(tmp_path / "new-key", b"n" * 32)),
            "--new-key-id",
            "new-v2",
            "--batch-size",
            "1",
        ],
        repository=repository,
        write_line=output.append,
    )

    assert code == 0
    assert output == ["Rewrapped 2 Firecrawl credential envelopes."]
    assert repository.batch_sizes == [1, 1, 1]
    new_vault = CredentialVault(new_provider)
    old_vault = CredentialVault(old_provider)
    for item, credential in zip(
        repository.records.values(),
        ("workspace-a-test-only-token", "workspace-b-test-only-token"),
        strict=True,
    ):
        before = original[(item.workspace_id, item.id)]
        after = item.credential_envelope
        assert before is not None and after is not None
        assert after.key_id == "new-v2"
        assert after.ciphertext == before.ciphertext
        assert after.nonce == before.nonce
        assert after.wrapped_data_key != before.wrapped_data_key
        assert (
            await new_vault.decrypt(after, item.workspace_id, item.id, item.credential_revision)
            == credential
        )
        with pytest.raises(CredentialUnavailableError):
            await old_vault.decrypt(after, item.workspace_id, item.id, item.credential_revision)
    serialized_output = "\n".join(output)
    assert "test-only-token" not in serialized_output
    assert "ciphertext" not in serialized_output
    assert "wrapped_data_key" not in serialized_output


@pytest.mark.parametrize(
    "extra_arguments",
    [
        ["--old-key-id", "same", "--new-key-id", "same"],
        ["--batch-size", "0"],
        ["--batch-size", "1001"],
    ],
)
async def test_rewrap_rejects_equal_ids_and_invalid_batch_sizes(
    tmp_path: Path,
    extra_arguments: list[str],
) -> None:
    old_key = key_file(tmp_path / "old-key", b"o" * 32)
    new_key = key_file(tmp_path / "new-key", b"n" * 32)
    base = [
        "--old-key-file",
        str(old_key),
        "--old-key-id",
        "old-v1",
        "--new-key-file",
        str(new_key),
        "--new-key-id",
        "new-v2",
        "--batch-size",
        "100",
    ]
    for index in range(0, len(extra_arguments), 2):
        option = extra_arguments[index]
        base[base.index(option) + 1] = extra_arguments[index + 1]
    repository = RewrapRepositoryTestDouble(())
    output: list[str] = []

    code = await run_rewrap_command(base, repository=repository, write_line=output.append)

    assert code == 2
    assert output == ["The Firecrawl credential rewrap request is invalid."]
    assert repository.records == {}


async def test_rewrap_rejects_an_envelope_owned_by_another_key(tmp_path: Path) -> None:
    actual_provider = FileKeyProvider.from_bytes(key_id="unexpected-v1", key=b"u" * 32)
    existing = await record(
        WORKSPACE_A,
        CONNECTION_A,
        "workspace-a-test-only-token",
        actual_provider,
    )
    repository = RewrapRepositoryTestDouble((existing,))
    output: list[str] = []

    code = await run_rewrap_command(
        [
            "--old-key-file",
            str(key_file(tmp_path / "old-key", b"o" * 32)),
            "--old-key-id",
            "old-v1",
            "--new-key-file",
            str(key_file(tmp_path / "new-key", b"n" * 32)),
            "--new-key-id",
            "new-v2",
            "--batch-size",
            "10",
        ],
        repository=repository,
        write_line=output.append,
    )

    assert code == 1
    assert output == ["Firecrawl credential rewrap failed safely."]
    assert repository.records[(WORKSPACE_A, CONNECTION_A)] == existing
