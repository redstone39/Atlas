from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

def validate_directory_transport(provider_type: str, tls_mode: str) -> None:
    if tls_mode not in {"ldaps", "start_tls", "plain"}:
        raise ValueError("unsupported directory transport")



@dataclass(frozen=True, slots=True)
class DirectoryPrincipal:
    external_subject: str
    username: str
    display_name: str
    email: str | None
    groups: tuple[str, ...]
    department: str | None
    title: str | None
    employee_id: str | None
    directory_enabled: bool | None


@dataclass(frozen=True, slots=True)
class DirectoryConnectionRecord:
    connection_id: str
    display_name: str
    priority: int
    provider_type: str
    host: str
    port: int
    tls_mode: str
    connect_timeout_seconds: int
    operation_timeout_seconds: int
    bind_dn: str
    user_base_dn: str
    user_object_filter: str
    login_attribute: str
    stable_id_attribute: str
    display_name_attribute: str
    email_attribute: str
    groups_attribute: str
    department_attribute: str
    title_attribute: str
    employee_id_attribute: str
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DirectorySecretRecord:
    connection_id: str
    secret_kind: str
    ciphertext: str
    nonce: str
    key_id: str
    version: int
    algorithm: str
    storage_backend: str
    updated_at: str


def directory_record_revision(
    record: DirectoryConnectionRecord | DirectorySecretRecord,
) -> str:
    payload = json.dumps(
        asdict(record),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class ExternalIdentityRecord:
    actor_id: str
    connection_id: str
    external_subject: str
    normalized_username: str
    normalized_email: str | None
    username: str
    display_name: str
    email: str | None
    groups: tuple[str, ...]
    department: str | None
    title: str | None
    employee_id: str | None
    directory_enabled: bool | None
    status: str
    last_refreshed_at: str


class DirectoryGatewayError(RuntimeError):
    """Safe directory failure that never retains transport diagnostics or secrets."""

    def __init__(self, code: str) -> None:
        if code not in {
            "invalid_credentials",
            "directory_unavailable",
            "directory_entry_invalid",
        }:
            raise ValueError("unsupported directory gateway error")
        self.code = code
        super().__init__(code)
