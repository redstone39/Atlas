import pytest
from pydantic import ValidationError
import httpx
from atlas_production.modules.notes.public import NotesSettingsUpdateRequestV1
from atlas_production.infrastructure.notes_collaboration_client import HttpNotesCollaborationClient
from atlas_production.modules.ops.service import OpsReadinessService
from atlas_production.shared.user_messages import validate_message_reference

def test_notes_002_settings_accept_positive_integer_without_product_cap():
    value=NotesSettingsUpdateRequestV1(checkpoint_interval_seconds=2**40,expected_settings_revision=1,idempotency_key="key")
    assert value.checkpoint_interval_seconds==2**40
    with pytest.raises(ValidationError): NotesSettingsUpdateRequestV1(checkpoint_interval_seconds=0,expected_settings_revision=1,idempotency_key="key")

class Repository:
    def refresh(self): pass
    def has_projects(self): return True
    def has_active_permission(self): return True
    def evidence_ready_project_ids(self): return ["project"]
    def processing_runner_available(self): return True
    def credential_encryption_available(self): return True
    def has_tested_model_route(self): return True
class Available:
    def readiness_available(self): return True
class Unavailable:
    def readiness_available(self): return False

def test_notes_002_readiness_names_collaboration_blocker():
    state=OpsReadinessService(Repository(),Available(),Unavailable()).readiness()
    assert "ops.notes_collaboration_is_unavailable" in state.setup_blockers
    for blocker in state.setup_blockers:
        assert validate_message_reference(blocker, {}) == {}


@pytest.mark.parametrize(
    "missing",
    [
        "ATLAS_NOTES_COLLABORATION_INTERNAL_URL",
        "ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET",
        "ATLAS_NOTES_COLLABORATION_PUBLIC_URL",
        "ATLAS_NOTES_COLLABORATION_TICKET_SECRET",
    ],
)
def test_notes_002_readiness_requires_every_collaboration_configuration(
    monkeypatch, missing
):
    values = {
        "ATLAS_NOTES_COLLABORATION_INTERNAL_URL": "http://carrier",
        "ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET": "transport",
        "ATLAS_NOTES_COLLABORATION_PUBLIC_URL": "ws://carrier",
        "ATLAS_NOTES_COLLABORATION_TICKET_SECRET": "api-only",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)
    assert HttpNotesCollaborationClient.from_environment().readiness_available() is False


def test_notes_002_readiness_requires_successful_carrier_probe(monkeypatch):
    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_INTERNAL_URL", "http://carrier")
    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET", "transport")
    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_PUBLIC_URL", "ws://carrier")
    monkeypatch.setenv("ATLAS_NOTES_COLLABORATION_TICKET_SECRET", "api-only")
    client = HttpNotesCollaborationClient.from_environment()
    assert "transport" not in repr(client)
    assert "api-only" not in repr(client)

    class Response:
        status_code = 503

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: Response())
    assert client.readiness_available() is False

    def unavailable(*_args, **_kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", unavailable)
    assert client.readiness_available() is False

    monkeypatch.setenv(
        "ATLAS_NOTES_COLLABORATION_TICKET_SECRET", "transport"
    )
    assert (
        HttpNotesCollaborationClient.from_environment().readiness_available()
        is False
    )
