from __future__ import annotations


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
    "identity_actor_owner_key",
    "project_acl_subject_owner_key",
    "project_owner_key",
    "team_owner_key",
    "team_subject_owner_key",
]
