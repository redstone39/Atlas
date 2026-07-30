from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from atlas_production.modules.model_routing.records import (
    ProviderConnectionSecretRecord,
)
from atlas_production.shared.public import utc_now_iso
from atlas_production.shared.user_messages import MessageParams, validate_message_reference


@dataclass
class CredentialCryptoError(RuntimeError):
    code: str
    message_code: str
    message_params: MessageParams = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.message_params = validate_message_reference(self.message_code, self.message_params)

    def __str__(self) -> str:
        return self.message_code


class AesGcmCredentialCipher:
    def __init__(
        self,
        *,
        key: bytes,
        key_id: str,
        decryption_keys: Mapping[str, bytes] | None = None,
    ) -> None:
        if len(key) != 32 or not key_id.strip():
            raise CredentialCryptoError(
                "credential_master_key_unavailable",
                'provider.credential_encryption_is_unavailable',
            )
        self._key = bytes(key)
        self.key_id = key_id.strip()
        keys = {
            identity.strip(): bytes(value)
            for identity, value in (decryption_keys or {}).items()
        }
        keys[self.key_id] = self._key
        if any(not identity or len(value) != 32 for identity, value in keys.items()):
            raise CredentialCryptoError(
                "credential_master_key_unavailable",
                'provider.credential_encryption_is_unavailable',
            )
        self._decryption_keys = keys

    @classmethod
    def from_environment(cls) -> "AesGcmCredentialCipher":
        encoded = os.getenv("ATLAS_CREDENTIAL_MASTER_KEY")
        key_id = os.getenv("ATLAS_CREDENTIAL_MASTER_KEY_ID")
        if not encoded or not key_id:
            raise CredentialCryptoError(
                "credential_master_key_unavailable",
                'provider.credential_encryption_is_unavailable',
            )
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
            raise CredentialCryptoError(
                "credential_master_key_unavailable",
                'provider.credential_encryption_is_unavailable',
            ) from exc
        return cls(key=key, key_id=key_id, decryption_keys=keyring)

    @staticmethod
    def _aad(connection_id: str, provider_type: str, secret_version: int) -> bytes:
        return json.dumps(
            {
                "connection_id": connection_id,
                "provider_type": provider_type,
                "secret_version": secret_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def encrypt(
        self,
        *,
        connection_id: str,
        provider_type: str,
        secret_version: int,
        plaintext: str,
    ) -> ProviderConnectionSecretRecord:
        if not plaintext:
            raise CredentialCryptoError(
                "provider_credential_unavailable",
                'provider.credential_is_unavailable',
            )
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(
            nonce,
            plaintext.encode("utf-8"),
            self._aad(connection_id, provider_type, secret_version),
        )
        return ProviderConnectionSecretRecord(
            connection_id=connection_id,
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
            nonce=base64.b64encode(nonce).decode("ascii"),
            key_id=self.key_id,
            version=secret_version,
            updated_at=utc_now_iso(),
        )

    def request_fingerprint(self, canonical_payload: bytes) -> str:
        """Return a stable, key-bound digest without persisting request secrets."""
        fingerprint_key = hmac.new(
            self._key,
            b"atlas-model-routing-idempotency-key-v1",
            hashlib.sha256,
        ).digest()
        return hmac.new(
            fingerprint_key,
            canonical_payload,
            hashlib.sha256,
        ).hexdigest()

    def decrypt(
        self,
        secret: ProviderConnectionSecretRecord,
        *,
        connection_id: str,
        provider_type: str,
    ) -> str:
        if (
            secret.connection_id != connection_id
            or secret.key_id not in self._decryption_keys
            or secret.storage_backend != "encrypted_database"
            or secret.algorithm != "AES-256-GCM"
            or secret.version < 1
        ):
            raise CredentialCryptoError(
                "provider_credential_unavailable",
                'provider.credential_is_unavailable',
            )
        try:
            nonce = base64.b64decode(secret.nonce, validate=True)
            ciphertext = base64.b64decode(secret.ciphertext, validate=True)
            if len(nonce) != 12:
                raise ValueError("invalid nonce")
            plaintext = AESGCM(self._decryption_keys[secret.key_id]).decrypt(
                nonce,
                ciphertext,
                self._aad(connection_id, provider_type, secret.version),
            )
            return plaintext.decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error, InvalidTag) as exc:
            raise CredentialCryptoError(
                "provider_credential_unavailable",
                'provider.credential_is_unavailable',
            ) from exc
