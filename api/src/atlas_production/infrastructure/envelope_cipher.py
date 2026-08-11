from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True, slots=True)
class EncryptedCredential:
    ciphertext: str
    nonce: str
    key_id: str
    version: int
    algorithm: str = "AES-256-GCM"
    storage_backend: str = "encrypted_database"


class AesGcmEnvelopeCipher:
    def __init__(
        self,
        *,
        key: bytes,
        key_id: str,
        decryption_keys: Mapping[str, bytes] | None = None,
    ) -> None:
        if len(key) != 32 or not key_id.strip():
            raise ValueError("credential master key is unavailable")
        self._key = bytes(key)
        self.key_id = key_id.strip()
        keys = {
            identity.strip(): bytes(value)
            for identity, value in (decryption_keys or {}).items()
        }
        keys[self.key_id] = self._key
        if any(not identity or len(value) != 32 for identity, value in keys.items()):
            raise ValueError("credential master key is unavailable")
        self._decryption_keys = keys

    @classmethod
    def from_environment(cls) -> "AesGcmEnvelopeCipher":
        encoded = os.getenv("ATLAS_CREDENTIAL_MASTER_KEY")
        key_id = os.getenv("ATLAS_CREDENTIAL_MASTER_KEY_ID")
        if not encoded or not key_id:
            raise ValueError("credential master key is unavailable")
        try:
            key = base64.b64decode(encoded, validate=True)
            raw_keyring = os.getenv("ATLAS_CREDENTIAL_MASTER_KEYRING", "{}")
            keyring_payload = json.loads(raw_keyring)
            if not isinstance(keyring_payload, dict) or any(
                not isinstance(identity, str) or not isinstance(value, str)
                for identity, value in keyring_payload.items()
            ):
                raise ValueError("credential keyring must be a string map")
            keyring = {
                identity: base64.b64decode(value, validate=True)
                for identity, value in keyring_payload.items()
            }
        except (ValueError, binascii.Error, json.JSONDecodeError) as exc:
            raise ValueError("credential master key is unavailable") from exc
        return cls(key=key, key_id=key_id, decryption_keys=keyring)

    @staticmethod
    def _aad(
        domain: str,
        owner_id: str,
        owner_kind: str,
        secret_version: int,
    ) -> bytes:
        return json.dumps(
            {
                "domain": domain,
                "owner_id": owner_id,
                "owner_kind": owner_kind,
                "secret_version": secret_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def encrypt(
        self,
        *,
        domain: str,
        owner_id: str,
        owner_kind: str,
        secret_version: int,
        plaintext: str,
    ) -> EncryptedCredential:
        if not domain or not owner_id or not owner_kind or secret_version < 1 or not plaintext:
            raise ValueError("credential envelope input is invalid")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(
            nonce,
            plaintext.encode("utf-8"),
            self._aad(domain, owner_id, owner_kind, secret_version),
        )
        return EncryptedCredential(
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
            nonce=base64.b64encode(nonce).decode("ascii"),
            key_id=self.key_id,
            version=secret_version,
        )

    def decrypt(
        self,
        secret: EncryptedCredential,
        *,
        domain: str,
        owner_id: str,
        owner_kind: str,
    ) -> str:
        if (
            not domain
            or not owner_id
            or not owner_kind
            or secret.key_id not in self._decryption_keys
            or secret.storage_backend != "encrypted_database"
            or secret.algorithm != "AES-256-GCM"
            or secret.version < 1
        ):
            raise ValueError("credential envelope is unavailable")
        try:
            nonce = base64.b64decode(secret.nonce, validate=True)
            ciphertext = base64.b64decode(secret.ciphertext, validate=True)
            if len(nonce) != 12:
                raise ValueError("invalid nonce")
            plaintext = AESGCM(self._decryption_keys[secret.key_id]).decrypt(
                nonce,
                ciphertext,
                self._aad(domain, owner_id, owner_kind, secret.version),
            )
            return plaintext.decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error, InvalidTag) as exc:
            raise ValueError("credential envelope is unavailable") from exc
