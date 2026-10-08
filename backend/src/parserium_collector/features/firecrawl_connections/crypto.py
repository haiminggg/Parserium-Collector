from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap

from parserium_collector.features.firecrawl_connections.crypto_encoding import (
    decode_canonical_base64,
    encode_canonical_base64,
)
from parserium_collector.features.firecrawl_connections.errors import (
    CredentialUnavailableError,
)
from parserium_collector.features.firecrawl_connections.models import (
    CredentialEnvelope,
    validate_credential,
)

_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def credential_aad(workspace_id: UUID, connection_id: UUID, revision: int) -> bytes:
    return json.dumps(
        {
            "algorithm": "AES-256-GCM",
            "connection_id": str(connection_id),
            "revision": revision,
            "version": 1,
            "workspace_id": str(workspace_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class FileKeyProvider:
    key_id: str
    _key: bytes = field(repr=False)

    @classmethod
    def from_bytes(cls, *, key_id: str, key: bytes) -> FileKeyProvider:
        if _KEY_ID.fullmatch(key_id) is None or len(key) != 32:
            raise CredentialUnavailableError
        return cls(key_id=key_id, _key=bytes(key))

    @classmethod
    def from_file(cls, *, key_id: str, path: Path) -> FileKeyProvider:
        try:
            if not path.is_file() or path.stat().st_size != 44:
                raise CredentialUnavailableError
            encoded = path.read_text(encoding="ascii")
            key = decode_canonical_base64(encoded)
        except CredentialUnavailableError:
            raise
        except (OSError, UnicodeError, ValueError):
            raise CredentialUnavailableError from None
        return cls.from_bytes(key_id=key_id, key=key)

    def wrap_data_key(self, data_key: bytes) -> bytes:
        if len(data_key) != 32:
            raise CredentialUnavailableError
        try:
            return aes_key_wrap(self._key, data_key)
        except Exception:
            raise CredentialUnavailableError from None

    def unwrap_data_key(self, wrapped_data_key: bytes) -> bytes:
        try:
            data_key = aes_key_unwrap(self._key, wrapped_data_key)
        except Exception:
            raise CredentialUnavailableError from None
        if len(data_key) != 32:
            raise CredentialUnavailableError
        return data_key


class CredentialVault:
    def __init__(self, provider: FileKeyProvider) -> None:
        self._provider = provider

    async def encrypt(
        self,
        credential: str,
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
    ) -> CredentialEnvelope:
        if revision < 1:
            raise ValueError("Credential revisions must be positive.")
        plaintext = validate_credential(credential).encode("utf-8")
        data_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ciphertext = AESGCM(data_key).encrypt(
            nonce,
            plaintext,
            credential_aad(workspace_id, connection_id, revision),
        )
        wrapped_data_key = self._provider.wrap_data_key(data_key)
        return CredentialEnvelope(
            key_id=self._provider.key_id,
            nonce=encode_canonical_base64(nonce),
            ciphertext=encode_canonical_base64(ciphertext),
            wrapped_data_key=encode_canonical_base64(wrapped_data_key),
        )

    async def decrypt(
        self,
        envelope: CredentialEnvelope | Mapping[str, object],
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
    ) -> str:
        try:
            parsed = CredentialEnvelope.model_validate(envelope)
            if parsed.key_id != self._provider.key_id or revision < 1:
                raise CredentialUnavailableError
            nonce = decode_canonical_base64(parsed.nonce)
            ciphertext = decode_canonical_base64(parsed.ciphertext)
            wrapped_data_key = decode_canonical_base64(parsed.wrapped_data_key)
            data_key = self._provider.unwrap_data_key(wrapped_data_key)
            plaintext = AESGCM(data_key).decrypt(
                nonce,
                ciphertext,
                credential_aad(workspace_id, connection_id, revision),
            )
            credential = plaintext.decode("utf-8")
            return validate_credential(credential)
        except Exception:
            raise CredentialUnavailableError from None


def rewrap_credential_envelope(
    envelope: CredentialEnvelope | Mapping[str, object],
    old_provider: FileKeyProvider,
    new_provider: FileKeyProvider,
) -> CredentialEnvelope:
    try:
        parsed = CredentialEnvelope.model_validate(envelope)
        if old_provider.key_id == new_provider.key_id or parsed.key_id != old_provider.key_id:
            raise CredentialUnavailableError
        data_key = old_provider.unwrap_data_key(decode_canonical_base64(parsed.wrapped_data_key))
        return parsed.model_copy(
            update={
                "key_id": new_provider.key_id,
                "wrapped_data_key": encode_canonical_base64(new_provider.wrap_data_key(data_key)),
            }
        )
    except Exception:
        raise CredentialUnavailableError from None
