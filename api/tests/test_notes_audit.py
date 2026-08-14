import pytest

from atlas_production.infrastructure.persistence.audit_events import _audit_metadata_payload
from atlas_production.infrastructure.persistence.payload_policy import PersistedPayloadPolicyError


def test_notes_002_safe_audit_accepts_only_identifiers_heads_digests_and_kind() -> None:
    payload=_audit_metadata_payload({"note_id":"n","category_id":"c","savepoint_id":"s",
        "revision":2,"savepoint_sequence":1,"collaboration_epoch":3,
        "settings_revision":4,"digest":"a"*64,"event_kind":"content_update"})
    assert payload["note_id"]=="n"


def test_notes_002_safe_audit_rejects_protected_note_bytes() -> None:
    for key in ("title","body","diff","raw_yjs_update","ticket","secret"):
        with pytest.raises(PersistedPayloadPolicyError):
            _audit_metadata_payload({key:"protected"})
