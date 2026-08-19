from __future__ import annotations
from hashlib import sha256
from atlas_production.modules.identity_access.directory_service import canonical_identifier


def _identity_value_owner_key(namespace: str, *values: str) -> str:
    digest = sha256("\0".join(values).encode("utf-8")).hexdigest()
    return f"identity:{namespace}:{digest}"


def identity_email_owner_key(email: str) -> str:
    """Canonical local/directory email lock without exposing the email."""

    digest = sha256(canonical_identifier(email).encode("utf-8")).hexdigest()
    return f"identity-email:{digest}"


def directory_alias_owner_key(connection_id: str, alias: str) -> str:
    """Canonical source alias lock shared by every directory import path."""

    return _identity_value_owner_key(
        "directory-alias",
        connection_id,
        alias,
    )


def directory_subject_owner_key(connection_id: str, external_subject: str) -> str:
    """Canonical source subject lock shared by every directory import path."""

    return _identity_value_owner_key(
        "directory-subject",
        connection_id,
        external_subject,
    )


def identity_actor_owner_key(actor_id: str) -> str:
    """Target Identity aggregate lock shared by actor readers and writers."""

    return f"identity:actor:{actor_id}"


def team_owner_key(team_id: str) -> str:
    """Target Team aggregate lock shared by hierarchy and ACL readers."""

    return f"team:team:{team_id}"


def team_subject_owner_key(actor_type: str, actor_id: str) -> str:
    """Target Team membership-subject lock for one actor."""

    return f"team:subject:{actor_type}:{actor_id}"


def project_owner_key(project_id: str) -> str:
    """Target Project aggregate lock shared by Project and ACL readers."""

    return f"project:project:{project_id}"


def project_acl_subject_owner_key(subject_type: str, subject_id: str) -> str:
    """Target Project ACL-subject lock for direct and Team grants."""

    return f"project:acl-subject:{subject_type}:{subject_id}"


__all__ = [
    "directory_alias_owner_key",
    "directory_subject_owner_key",
    "identity_email_owner_key",
    "identity_actor_owner_key",
    "project_acl_subject_owner_key",
    "project_owner_key",
    "team_owner_key",
    "team_subject_owner_key",
]
