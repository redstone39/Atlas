from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace
from dataclasses import asdict

import pytest

from atlas_production.infrastructure import (
    postgres_artifact_journeys as journeys,
    postgres_artifact_storage_adapter as adapter,
)
from atlas_production.infrastructure.postgres_owner import artifact as owner
from atlas_production.infrastructure.persistence.artifact_storage import AtlasArtifactRow
from atlas_production.modules.artifact_storage.records import ArtifactRecord, StorageFence
from atlas_production.modules.identity_access.records import (
    AccessDecisionRecord,
    UserRecord,
)
from atlas_production.shared.public import AuditEventRecord
from atlas_production.modules.document_intake.records import DocumentVersionRecord


NOW = "2026-07-18T00:00:00+00:00"
TOKEN = "browser-token-do-not-log"
DIGEST = hashlib.sha256(b"abc").hexdigest()
FENCE = StorageFence("target-1", 1, "f" * 64, 1)

def test_artifact_row_record_maps_persisted_metadata_json() -> None:
    metadata = {"source_filename": "manual.pdf"}
    row = AtlasArtifactRow(
        artifact_id="artifact-1",
        artifact_class="original_document",
        blob_id="blob-1",
        checksum_algorithm="sha256",
        checksum_value=DIGEST,
        byte_size=3,
        content_type="application/pdf",
        owner_scope_type="project",
        owner_scope_id="project-1",
        lifecycle_status="active",
        logical_identity="original:document-1",
        metadata_json=metadata,
        created_at=NOW,
        updated_at=NOW,
    )

    record = journeys._row_record(row, ArtifactRecord)

    assert record.metadata == metadata
    assert record.metadata is not metadata



class Authority:
    def effective_document_scope(self, **_kwargs):
        return {("project", "project-1")}


def _decision(*, allowed: bool, reason: str) -> AccessDecisionRecord:
    return AccessDecisionRecord(
        "decision-1", "user", "user-1", "project-1", "read_original",
        "viewer", allowed, reason, "viewer" if allowed else None, "user",
        "user-1", "exact request-boundary result", NOW,
        scope_type="project", scope_id="project-1",
    )


def _audit(decision_id: str) -> AuditEventRecord:
    return AuditEventRecord(
        "audit-1", "artifact_read", "user-1", "artifact:artifact-1",
        "project-1", "project.is_ready_for_membership_setup",
        {"access_decision_id": decision_id}, NOW,
    )


def _protected_facts(
    *,
    method: str,
    source_restricted: bool = False,
    allow_member_download: bool = True,
    system_role: str = "user",
    can_administer_owner_scope: bool = False,
) -> journeys.ProtectedOriginalFacts:
    reason = (
        "source_download_restricted"
        if source_restricted
        else "member_download_policy"
        if not allow_member_download
        and not can_administer_owner_scope
        and system_role != "admin"
        else "project_grant"
    )
    allowed = reason == "project_grant"
    decision = None if method == "HEAD" and allowed else _decision(
        allowed=allowed, reason=reason
    )
    return journeys.ProtectedOriginalFacts(
        actor=UserRecord(
            "user-1", "User", None, "member", None, True, system_role, NOW
        ),
        presented_browser_session_token=TOKEN,
        action="read_original",
        method=method,
        document=SimpleNamespace(
            document_id="document-1", lifecycle_status="active",
            source_download_restricted=source_restricted,
            original_artifact_id="artifact-1", source_kind="file_upload",
            resource_lifecycle_epoch=1, scope_type="project",
            scope_id="project-1", allow_member_download=allow_member_download,
        ),
        version=SimpleNamespace(
            document_version_id="version-1", document_id="document-1",
            status="active", original_artifact_id="artifact-1",
            content_type="application/pdf",
        ),
        tags=(SimpleNamespace(
            document_id="document-1", tag_type="project", tag_id="project-1"
        ),),
        artifact=SimpleNamespace(
            artifact_id="artifact-1", artifact_class="original_document",
            lifecycle_status="active", document_version_id="version-1",
            parent_resource_id="document-1", parent_lifecycle_epoch=1,
            owner_scope_type="project", owner_scope_id="project-1",
            blob_id="blob-1", checksum_value=DIGEST, byte_size=3,
            content_type="application/pdf",
        ),
        blob=SimpleNamespace(
            blob_id="blob-1", status="committed", fence=FENCE,
            checksum_algorithm="sha256", checksum_value=DIGEST, byte_size=3,
            content_type="application/pdf",
        ),
        bindings=(SimpleNamespace(
            artifact_id="artifact-1", binding_kind="owner",
            scope_type="project", scope_id="project-1",
        ),),
        candidate_team_ids=frozenset(),
        can_administer_owner_scope=(
            can_administer_owner_scope or system_role == "admin"
        ),
        observed_at=NOW,
        read_lease=SimpleNamespace(fence=FENCE),
        access_decision=decision,
        audit_events=() if decision is None else (_audit(decision.decision_id),),
        filename="manual.pdf",
    )


class DeniedCommand:
    def __init__(self, events: list[str]):
        self.events = events

    def execute(self, request):
        self.events.append(f"commit-denial:{request.access_decision.reason}")
        raise owner.ArtifactProtectedOpenDenied("denied")


class NeverFilesystem:
    def open_read(self, *_args, **_kwargs):
        raise AssertionError("denial must not open bytes")


def _denial_adapter(events: list[str]) -> adapter.PostgresArtifactStorageAdapter:
    unused = SimpleNamespace(execute=lambda _request: None)
    return adapter.PostgresArtifactStorageAdapter(
        DeniedCommand(events), unused, unused, unused, unused, unused,
        NeverFilesystem(),
    )


@pytest.mark.parametrize(
    ("method", "facts", "reason"),
    (
        ("GET", _protected_facts(method="GET", source_restricted=True),
         "source_download_restricted"),
        ("HEAD", _protected_facts(method="HEAD", allow_member_download=False),
         "member_download_policy"),
    ),
)
def test_policy_denial_reaches_owner_before_headers_or_bytes(
    method: str,
    facts: journeys.ProtectedOriginalFacts,
    reason: str,
) -> None:
    built = journeys.ProtectedOriginalJourneyBuilder(Authority()).build(facts)
    events: list[str] = []

    with pytest.raises(owner.ArtifactProtectedOpenDenied):
        _denial_adapter(events).open_original(
            built.request, method=method, filename=built.filename
        )

    assert built.request.presented_browser_session_token == TOKEN
    assert built.request.access_decision.reason == reason
    assert events == [f"commit-denial:{reason}"]


@pytest.mark.parametrize("method", ("GET", "HEAD"))
def test_owner_scope_admin_bypasses_only_member_download_policy(
    method: str,
) -> None:
    facts = _protected_facts(
        method=method,
        allow_member_download=False,
        can_administer_owner_scope=True,
    )

    built = journeys.ProtectedOriginalJourneyBuilder(Authority()).build(facts)

    assert built.request.expected_can_administer_owner_scope is True
    assert (built.request.access_decision is None) is (method == "HEAD")
    if method == "HEAD":
        assert built.request.audit_events == ()
    else:
        assert built.request.audit_events


def test_protected_original_provider_rejects_missing_authorization_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _protected_facts(method="GET")
    version = DocumentVersionRecord(
        "version-1", "document-1", "Manual", "file_upload", "pdf",
        "a" * 64, "b" * 64, NOW,
        original_artifact_id="artifact-1", content_type="application/pdf",
    )
    document_row = SimpleNamespace(
        document_id="document-1", original_artifact_id="artifact-1"
    )
    artifact_row = SimpleNamespace(artifact_id="artifact-1", blob_id="blob-1")
    blob_row = SimpleNamespace(
        blob_id="blob-1", opaque_ref="blobs/aa/bb/blob.blob", status="committed",
        dedup_mode="none", dedup_scope_type=None, dedup_scope_id=None,
        checksum_algorithm="sha256", checksum_value=DIGEST, byte_size=3,
        content_type="application/pdf", target_id="target-1", target_revision=1,
        root_identity_digest="f" * 64, storage_epoch=1, created_at=NOW,
        updated_at=NOW, write_attempt_id=None, committed_at=NOW,
        failure_code=None, failure_detail_summary=None,
        reconciliation_required_at=None, reconciled_at=None, reconciled_by=None,
    )

    class ScalarResult:
        def __init__(self, values): self.values = values
        def all(self): return self.values

    class Session:
        def __init__(self): self.results = [
            [SimpleNamespace(payload=asdict(version))],
            [SimpleNamespace(
                document_id="document-1", tag_type="project",
                tag_id="project-1", created_at=NOW,
            )],
            [],
        ]
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, row_type, key):
            name = row_type.__name__
            return document_row if name == "AtlasDocumentRow" else artifact_row if name == "AtlasArtifactRow" else blob_row
        def scalars(self, _statement): return ScalarResult(self.results.pop(0))

    monkeypatch.setattr(journeys, "read_session_actor", lambda *_args: facts.actor)
    monkeypatch.setattr(
        journeys,
        "_row_record",
        lambda _row, record_type: facts.document if record_type.__name__ == "DocumentRecord" else facts.artifact,
    )
    monkeypatch.setattr(
        journeys.ProtectedOriginalPreimageDenialCommand,
        "execute",
        lambda *_args, **_kwargs: SimpleNamespace(event_id="audit-binding-denied"),
    )
    provider = journeys.PostgresProtectedOriginalJourneyProvider(Session)
    with pytest.raises(journeys.ProtectedOriginalUnavailable) as captured:
        provider.load(
            document_id="document-1",
            presented_browser_session_token=TOKEN,
            method="GET",
        )
    assert captured.value.audit_event_ref == "audit-binding-denied"


def test_protected_original_preimage_denial_rolls_back_when_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _protected_facts(method="GET")

    class Session:
        commits = 0
        rollbacks = 0
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def commit(self): self.commits += 1
        def rollback(self): self.rollbacks += 1

    session = Session()
    monkeypatch.setattr(journeys.AccessDecisionWriter, "append", lambda *_args: None)
    monkeypatch.setattr(
        journeys.AuditEventWriter,
        "append_many",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )
    command = journeys.ProtectedOriginalPreimageDenialCommand(lambda: session)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        command.execute(
            actor=facts.actor,
            document=facts.document,
            artifact_id="artifact-1",
            reason="authorization_binding_unavailable",
        )
    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.parametrize(
    ("scope_type", "scope_id", "method"),
    (
        ("team", "team-1", "GET"),
        ("team", "team-1", "HEAD"),
        ("project", "project-1", "GET"),
        ("project", "project-1", "HEAD"),
    ),
)
def test_protected_original_provider_success_reaches_terminal_open_command(
    monkeypatch: pytest.MonkeyPatch,
    scope_type: str,
    scope_id: str,
    method: str,
) -> None:
    facts = _protected_facts(method=method)
    facts.document.scope_type = scope_type
    facts.document.scope_id = scope_id
    facts.document.allow_member_download = False
    facts.artifact.owner_scope_type = scope_type
    facts.artifact.owner_scope_id = scope_id
    facts.document.source_filename = "manual.pdf"
    facts.document.title = "Manual"
    version = DocumentVersionRecord(
        "version-1", "document-1", "Manual", "file_upload", "pdf",
        "a" * 64, "b" * 64, NOW,
        original_artifact_id="artifact-1", content_type="application/pdf",
    )
    document_row = SimpleNamespace(document_id="document-1", original_artifact_id="artifact-1")
    artifact_row = SimpleNamespace(artifact_id="artifact-1", blob_id="blob-1")
    blob_row = SimpleNamespace(
        blob_id="blob-1", opaque_ref="blobs/aa/bb/blob.blob", status="committed",
        dedup_mode="none", dedup_scope_type=None, dedup_scope_id=None,
        checksum_algorithm="sha256", checksum_value=DIGEST, byte_size=3,
        content_type="application/pdf", target_id="target-1", target_revision=1,
        root_identity_digest="f" * 64, storage_epoch=1, created_at=NOW,
        updated_at=NOW, write_attempt_id=None, committed_at=NOW,
        failure_code=None, failure_detail_summary=None,
        reconciliation_required_at=None, reconciled_at=None, reconciled_by=None,
    )
    binding = SimpleNamespace(
        binding_id="binding-1", artifact_id="artifact-1", binding_kind="owner",
        scope_type=scope_type, scope_id=scope_id, created_at=NOW,
    )

    class ScalarResult:
        def __init__(self, values): self.values = values
        def all(self): return self.values
    class Session:
        def __init__(self): self.results = [
            [SimpleNamespace(payload=asdict(version))],
            [SimpleNamespace(
                document_id="document-1",
                tag_type=scope_type,
                tag_id=scope_id,
                created_at=NOW,
            )],
            [binding],
        ]
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def get(self, row_type, _key):
            return document_row if row_type.__name__ == "AtlasDocumentRow" else artifact_row if row_type.__name__ == "AtlasArtifactRow" else blob_row
        def scalars(self, _statement): return ScalarResult(self.results.pop(0))

    monkeypatch.setattr(journeys, "read_session_actor", lambda *_args: facts.actor)
    captured_owner: dict[str, str] = {}

    def resolve_scope(*_args, requested_scope, **kwargs):
        captured_owner["scope_type"] = kwargs["owner_scope_type"]
        captured_owner["scope_id"] = kwargs["owner_scope_id"]
        return (
            set(requested_scope),
            {scope_id} if scope_type == "team" else set(),
            True,
        )

    monkeypatch.setattr(
        journeys,
        "read_effective_document_scope_with_team_ids",
        resolve_scope,
    )
    scope_authority = SimpleNamespace(
        effective_document_scope=lambda **_kwargs: {(scope_type, scope_id)}
    )
    monkeypatch.setattr(
        journeys,
        "ActionAwareAclAuthority",
        lambda _factory: scope_authority,
    )
    monkeypatch.setattr(
        journeys,
        "_row_record",
        lambda _row, record_type: (
            facts.document if record_type.__name__ == "DocumentRecord"
            else binding if record_type.__name__ == "ArtifactScopeBindingRecord"
            else facts.artifact
        ),
    )
    provider = journeys.PostgresProtectedOriginalJourneyProvider(Session)
    built = provider.build(
        document_id="document-1",
        presented_browser_session_token=TOKEN,
        method=method,
    )
    assert built.request.expected_can_administer_owner_scope is True
    assert captured_owner == {"scope_type": scope_type, "scope_id": scope_id}
    if method == "GET":
        assert built.request.access_decision is not None
        assert built.request.access_decision.allowed is True
        assert built.request.audit_events
    else:
        assert built.request.access_decision is None
        assert built.request.audit_events == ()
    if method == "HEAD":
        calls = []

        class Terminal:
            def execute(self, request):
                calls.append(request)
                return owner.PostCommitArtifactOpener(
                    "artifact-1", "blob-1", "blobs/aa/bb/blob.blob", 3,
                    DIGEST, "application/pdf", request.read_lease,
                )

            def complete(self, _request):
                calls.append("complete")

        unused = SimpleNamespace(execute=lambda _request: None)
        service = adapter.PostgresArtifactStorageAdapter(
            Terminal(), unused, unused, unused, unused, unused, NeverFilesystem(),
        )
        response = service.open_original(
            built.request, method="HEAD", filename=built.filename,
        )
        assert response.status_code == 200
        assert calls[0] is built.request
        assert calls[1] == "complete"


def test_successful_get_and_head_build_exact_transport_inputs() -> None:
    get_facts = replace(
        _protected_facts(method="GET"),
        if_match='"match"', if_none_match='"none"', if_range='"range"',
        range_header="bytes=1-2",
    )
    builder = journeys.ProtectedOriginalJourneyBuilder(Authority())

    get = builder.build(get_facts)
    head = builder.build(_protected_facts(method="HEAD"))

    assert get.request.presented_browser_session_token == TOKEN
    assert get.request.record_success_evidence is True
    assert get.request.access_decision == get_facts.access_decision
    assert get.request.audit_events == get_facts.audit_events
    assert (
        get.if_match, get.if_none_match, get.if_range, get.range_header
    ) == ('"match"', '"none"', '"range"', "bytes=1-2")
    assert head.method == "HEAD"
    assert head.request.record_success_evidence is False
    assert head.request.access_decision is None
    assert head.request.audit_events == ()
    assert head.request.presented_browser_session_token == TOKEN


class RecordingCommand:
    def __init__(self, events: list[str], label: str):
        self.events = events
        self.label = label
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        self.events.append(self.label)
        return owner.CommandResult()


class UploadFilesystem:
    def __init__(self, events: list[str]):
        self.events = events

    def write_temp(self, _name, chunks, *, max_bytes, progress_callback=None):
        payload = b"".join(chunks)
        assert len(payload) <= max_bytes and progress_callback is not None
        self.events.append("write")
        progress_callback(1)
        progress_callback(len(payload))
        return len(payload), hashlib.sha256(payload).hexdigest()

    def publish_no_overwrite(self, _temp, _ref):
        self.events.append("publish")

    def verify_full(self, _ref, **_kwargs):
        self.events.append("verify")

    def remove_temp(self, _name):
        self.events.append("cleanup")


def _upload_facts() -> journeys.ArtifactUploadFacts:
    now = datetime.now(timezone.utc)
    return journeys.ArtifactUploadFacts(
        write_attempt_id="attempt-1", lease_id="lease-1",
        idempotency_scope="upload", idempotency_key="key-1",
        request_fingerprint="r" * 64, fence=FENCE,
        parent_resource_id="document-1", parent_lifecycle_epoch=1,
        worker_id="worker-1",
        lease_expires_at=(now + timedelta(seconds=30)).isoformat(),
        observed_at=(now - timedelta(seconds=1)).isoformat(),
        opaque_temp_name="temp-1", artifact_id="artifact-1",
        blob_id="blob-1", opaque_ref="objects/blob-1",
        logical_identity="document-1:original", document_version_id="version-1",
        content_type="application/pdf", owner_scope_type="project",
        owner_scope_id="project-1",
        authorization_bindings=(("project", "project-1"),),
        owner_binding_id="binding-owner",
        authorization_binding_ids=("binding-auth",), chunks=(b"abc",),
        max_bytes=10, begin_audit_events=(_audit("decision-begin"),),
        finalize_audit_events=(_audit("decision-final"),),
        expected_parent=owner.DocumentParentCurrentness(
            "document-1", "active", 1, 0
        ),
    )


def test_upload_plan_heartbeats_moving_preimages_during_byte_io() -> None:
    events: list[str] = []
    begin = RecordingCommand(events, "begin")
    heartbeat = RecordingCommand(events, "heartbeat")
    finalize = RecordingCommand(events, "finalize")
    unused = RecordingCommand(events, "unused")
    service = adapter.PostgresArtifactStorageAdapter(
        unused, begin, finalize, unused, unused, unused,
        UploadFilesystem(events), heartbeat,
    )
    journey = journeys.ArtifactUploadJourneyBuilder().build(_upload_facts())

    service.write_artifact_plan(journey.plan)

    assert events == [
        "begin", "write", "heartbeat", "heartbeat", "publish", "verify",
        "finalize",
    ]
    first, second = heartbeat.requests
    assert second.expected_attempt == first.attempt
    assert second.expected_lease == first.lease
    terminal = finalize.requests[0]
    assert terminal.expected_attempt == second.attempt
    assert terminal.expected_lease == second.lease
    assert terminal.expected_parent == owner.DocumentParentCurrentness(
        "document-1", "active", 1, 0
    )
    assert terminal.blob.byte_size == 3
    assert terminal.blob.checksum_value == DIGEST
    for request in heartbeat.requests:
        observed = datetime.fromisoformat(request.observed_at)
        expected_expiry = datetime.fromisoformat(request.expected_lease.expires_at)
        next_expiry = datetime.fromisoformat(request.lease.expires_at)
        assert expected_expiry > observed
        assert next_expiry > expected_expiry
        assert next_expiry <= observed + timedelta(seconds=90)


@pytest.mark.parametrize(
    "parent",
    (
        owner.DocumentParentCurrentness("document-other", "active", 1, 0),
        owner.DocumentParentCurrentness("document-1", "disabled", 1, 0),
        owner.DocumentParentCurrentness("document-1", "active", 2, 0),
    ),
)
def test_upload_builder_rejects_noncurrent_parent_before_byte_io(parent) -> None:
    with pytest.raises(journeys.ArtifactJourneyCurrentnessError):
        journeys.ArtifactUploadJourneyBuilder().build(
            replace(_upload_facts(), expected_parent=parent)
        )

    with pytest.raises(journeys.ArtifactJourneyCurrentnessError):
        journeys.ArtifactUploadJourneyBuilder().build(
            replace(_upload_facts(), processing_generation=1)
        )


def test_operator_builders_pin_exact_blob_set_and_generation() -> None:
    blob_b = SimpleNamespace(
        blob_id="blob-b", opaque_ref="objects/blob-b",
        checksum_algorithm="sha256", checksum_value="b" * 64, byte_size=5,
    )
    blob_a = SimpleNamespace(
        blob_id="blob-a", opaque_ref="objects/blob-a",
        checksum_algorithm="sha256", checksum_value="a" * 64, byte_size=3,
    )
    facts = journeys.ArtifactTargetFacts(
        expected_control=None, committed_blobs=(blob_b, blob_a), target_id="offline-1",
        target_revision=1, target_kind="local", masked_label="offline",
        config_key="ATLAS_ARTIFACT_ROOT", root_identity_digest="f" * 64,
        capabilities={"range_read": True}, created_by="admin-1",
        operation_id="operation-1", idempotency_scope="target",
        idempotency_key="key-1", request_fingerprint="r" * 64,
        observed_at=NOW, audit_events=(_audit("decision-1"),),
        verification_mode="full_hash",
        evidence_claim="TARGET_COPY_CHECKSUM_VERIFIED",
    )
    builder = journeys.ArtifactTargetJourneyBuilder()

    offline = builder.offline(facts)
    portainer = builder.portainer(
        replace(facts, target_id="atlas-smb-1", target_kind="smb"),
        generation_prefix="atlas-smb-", switch_mode="explicit",
        risk_acknowledgement=None,
    )

    expected = (blob_a, blob_b)
    expected_digest = hashlib.sha256(
        b'[["blob-a","objects/blob-a","sha256","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",3],["blob-b","objects/blob-b","sha256","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",5]]'
    ).hexdigest()
    assert offline.command.expected_committed_blobs == expected
    assert offline.committed_blobs == expected
    assert offline.command.operation.committed_blob_count == 2
    assert offline.command.operation.total_bytes == 8
    assert offline.command.operation.blob_set_digest == expected_digest
    assert portainer.command.expected_committed_blobs == expected
    assert portainer.committed_blobs == expected
    assert portainer.command.operation.committed_blob_count == 2
    assert portainer.command.operation.total_bytes == 8
    assert portainer.command.operation.blob_set_digest == expected_digest
    assert portainer.command.monotonic_generation == 1
    assert portainer.command.generation_prefix == "atlas-smb-"
