from __future__ import annotations

import base64

from atlas_production.infrastructure.envelope_cipher import AesGcmEnvelopeCipher


def test_empty_optional_keyring_uses_current_master_key(monkeypatch) -> None:
    key = b"k" * 32
    monkeypatch.setenv("ATLAS_CREDENTIAL_MASTER_KEY", base64.b64encode(key).decode())
    monkeypatch.setenv("ATLAS_CREDENTIAL_MASTER_KEY_ID", "current")
    monkeypatch.setenv("ATLAS_CREDENTIAL_MASTER_KEYRING", "")

    cipher = AesGcmEnvelopeCipher.from_environment()
    encrypted = cipher.encrypt(
        domain="provider-credential",
        owner_id="provider-1",
        owner_kind="openai",
        secret_version=1,
        plaintext="secret",
    )

    assert cipher.decrypt(
        encrypted,
        domain="provider-credential",
        owner_id="provider-1",
        owner_kind="openai",
    ) == "secret"
