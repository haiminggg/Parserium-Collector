import base64
import json
from pathlib import Path
from uuid import UUID

import pytest

from parserium_collector.features.firecrawl_connections.crypto import (
    CredentialVault,
    FileKeyProvider,
    credential_aad,
)
from parserium_collector.features.firecrawl_connections.errors import (
    CredentialUnavailableError,
)

WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000003")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000004")
CONNECTION_A = UUID("20000000-0000-4000-8000-000000000003")
CONNECTION_B = UUID("20000000-0000-4000-8000-000000000004")
TEST_ONLY_TOKEN = "test-only-token-do-not-log"  # noqa: S105


def provider(*, key_id: str = "test-key", key: bytes = b"k" * 32) -> FileKeyProvider:
    return FileKeyProvider.from_bytes(key_id=key_id, key=key)


async def test_envelope_is_randomized_and_bound_to_workspace_and_revision() -> None:
    vault = CredentialVault(provider())
    first = await vault.encrypt(TEST_ONLY_TOKEN, WORKSPACE_A, CONNECTION_A, 1)
    second = await vault.encrypt(TEST_ONLY_TOKEN, WORKSPACE_A, CONNECTION_A, 1)

    assert first != second
    assert await vault.decrypt(first, WORKSPACE_A, CONNECTION_A, 1) == TEST_ONLY_TOKEN
    assert first.version == 1
    assert first.algorithm == "AES-256-GCM"
    assert first.wrapping_algorithm == "AES-256-KW"
    assert first.key_id == "test-key"

    for workspace_id, connection_id, revision in (
        (WORKSPACE_B, CONNECTION_A, 1),
        (WORKSPACE_A, CONNECTION_B, 1),
        (WORKSPACE_A, CONNECTION_A, 2),
    ):
        with pytest.raises(CredentialUnavailableError):
            await vault.decrypt(first, workspace_id, connection_id, revision)


def test_credential_aad_is_canonical_and_contains_only_binding_metadata() -> None:
    decoded = credential_aad(WORKSPACE_A, CONNECTION_A, 7).decode("utf-8")

    assert decoded == json.dumps(
        {
            "algorithm": "AES-256-GCM",
            "connection_id": str(CONNECTION_A),
            "revision": 7,
            "version": 1,
            "workspace_id": str(WORKSPACE_A),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert TEST_ONLY_TOKEN not in decoded


def tamper_binary(value: str) -> str:
    decoded = bytearray(base64.b64decode(value, validate=True))
    decoded[0] ^= 1
    return base64.b64encode(decoded).decode("ascii")


@pytest.mark.parametrize("field", ["nonce", "ciphertext", "wrapped_data_key"])
async def test_envelope_rejects_authenticated_binary_tampering(field: str) -> None:
    vault = CredentialVault(provider())
    envelope = await vault.encrypt(TEST_ONLY_TOKEN, WORKSPACE_A, CONNECTION_A, 1)
    payload = envelope.model_dump()
    payload[field] = tamper_binary(payload[field])

    with pytest.raises(CredentialUnavailableError) as raised:
        await vault.decrypt(payload, WORKSPACE_A, CONNECTION_A, 1)

    assert TEST_ONLY_TOKEN not in str(raised.value)
    assert payload[field] not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 2),
        ("algorithm", "AES-128-GCM"),
        ("wrapping_algorithm", "RSA-OAEP"),
        ("key_id", "other-key"),
        ("nonce", "not-base64!"),
        ("ciphertext", "YQ==\n"),
        ("wrapped_data_key", "YQ==="),
    ],
)
async def test_envelope_rejects_unknown_or_malformed_fields(
    field: str,
    value: object,
) -> None:
    vault = CredentialVault(provider())
    envelope = await vault.encrypt(TEST_ONLY_TOKEN, WORKSPACE_A, CONNECTION_A, 1)
    payload = envelope.model_dump()
    payload[field] = value

    with pytest.raises(CredentialUnavailableError) as raised:
        await vault.decrypt(payload, WORKSPACE_A, CONNECTION_A, 1)

    assert str(raised.value) == "The Firecrawl credential is unavailable."


async def test_envelope_rejects_the_wrong_wrapping_key() -> None:
    envelope = await CredentialVault(provider()).encrypt(
        TEST_ONLY_TOKEN,
        WORKSPACE_A,
        CONNECTION_A,
        1,
    )
    wrong_vault = CredentialVault(provider(key=b"w" * 32))

    with pytest.raises(CredentialUnavailableError):
        await wrong_vault.decrypt(envelope, WORKSPACE_A, CONNECTION_A, 1)


def test_file_key_provider_loads_one_strict_canonical_256_bit_key(tmp_path: Path) -> None:
    key_file = tmp_path / "wrapping-key"
    key_file.write_bytes(base64.b64encode(b"k" * 32))

    loaded = FileKeyProvider.from_file(key_id="hosted-v1", path=key_file)

    assert loaded.key_id == "hosted-v1"
    assert "kkkk" not in repr(loaded)


@pytest.mark.parametrize(
    "contents",
    [
        b"not-base64!",
        base64.b64encode(b"k" * 32) + b"\n",
        base64.b64encode(b"k" * 31),
        base64.b64encode(b"k" * 33),
    ],
)
def test_file_key_provider_rejects_malformed_or_weak_files(
    tmp_path: Path,
    contents: bytes,
) -> None:
    key_file = tmp_path / "wrapping-key"
    key_file.write_bytes(contents)

    with pytest.raises(CredentialUnavailableError):
        FileKeyProvider.from_file(key_id="hosted-v1", path=key_file)


def test_file_key_provider_rejects_missing_file_directory_and_weak_bytes(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()

    for path in (tmp_path / "missing", directory):
        with pytest.raises(CredentialUnavailableError):
            FileKeyProvider.from_file(key_id="hosted-v1", path=path)
    with pytest.raises(CredentialUnavailableError):
        FileKeyProvider.from_bytes(key_id="hosted-v1", key=b"short")


@pytest.mark.parametrize("key_id", ["", " key", "key\nvalue", "x" * 129])
def test_file_key_provider_rejects_unsafe_key_identifiers(key_id: str) -> None:
    with pytest.raises(CredentialUnavailableError):
        provider(key_id=key_id)
