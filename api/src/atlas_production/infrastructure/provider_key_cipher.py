from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Mapping

from atlas_production.infrastructure.envelope_cipher import (
    AesGcmEnvelopeCipher,
    EncryptedCredential,
)
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


def _provider_crypto_error(code: str) -> CredentialCryptoError:
    return CredentialCryptoError(
        code,
        (
            "provider.credential_encryption_is_unavailable"
            if code == "credential_master_key_unavailable"
            else "provider.credential_is_unavailable"
        ),
    )


class AesGcmCredentialCipher:
    def __init__(
        self,
        *,
        key: bytes,
        key_id: str,
        decryption_keys: Mapping[str, bytes] | None = None,
    ) -> None:
        try:
            self._envelope = AesGcmEnvelopeCipher(
                key=key,
                key_id=key_id,
                decryption_keys=decryption_keys,
            )
        except ValueError as exc:
            raise _provider_crypto_error("credential_master_key_unavailable") from exc

    @classmethod
    def from_environment(cls) -> "AesGcmCredentialCipher":
        try:
            envelope = AesGcmEnvelopeCipher.from_environment()
        except ValueError as exc:
            raise _provider_crypto_error("credential_master_key_unavailable") from exc
        instance = cls.__new__(cls)
        instance._envelope = envelope
        return instance

    def encrypt(
        self,
        *,
        connection_id: str,
        provider_type: str,
        secret_version: int,
        plaintext: str,
    ) -> ProviderConnectionSecretRecord:
        try:
            encrypted = self._envelope.encrypt(
                domain="model_routing_provider_credential",
                owner_id=connection_id,
                owner_kind=provider_type,
                secret_version=secret_version,
                plaintext=plaintext,
            )
        except ValueError as exc:
            raise _provider_crypto_error("provider_credential_unavailable") from exc
        return ProviderConnectionSecretRecord(
            connection_id=connection_id,
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            key_id=encrypted.key_id,
            version=encrypted.version,
            algorithm=encrypted.algorithm,
            storage_backend=encrypted.storage_backend,
            updated_at=utc_now_iso(),
        )

    def decrypt(
        self,
        secret: ProviderConnectionSecretRecord,
        *,
        connection_id: str,
        provider_type: str,
    ) -> str:
        if secret.connection_id != connection_id:
            raise _provider_crypto_error("provider_credential_unavailable")
        try:
            return self._envelope.decrypt(
                EncryptedCredential(
                    ciphertext=secret.ciphertext,
                    nonce=secret.nonce,
                    key_id=secret.key_id,
                    version=secret.version,
                    algorithm=secret.algorithm,
                    storage_backend=secret.storage_backend,
                ),
                domain="model_routing_provider_credential",
                owner_id=connection_id,
                owner_kind=provider_type,
            )
        except ValueError as exc:
            raise _provider_crypto_error("provider_credential_unavailable") from exc


def model_routing_request_fingerprint(canonical_payload: bytes) -> str:
    """Return the existing model-routing key-bound replay digest."""
    try:
        envelope = AesGcmEnvelopeCipher.from_environment()
    except ValueError as exc:
        raise _provider_crypto_error("credential_master_key_unavailable") from exc
    fingerprint_key = hmac.new(
        envelope._key,
        b"atlas-model-routing-idempotency-key-v1",
        hashlib.sha256,
    ).digest()
    return hmac.new(
        fingerprint_key,
        canonical_payload,
        hashlib.sha256,
    ).hexdigest()
