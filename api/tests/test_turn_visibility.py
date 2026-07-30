from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from atlas_production.modules.authorization.public import VisibilityDecisionV1
from atlas_production.modules.audit.public import TurnAuditDraftV2
from atlas_production.modules.citation_preview.public import (
    CitationBindingDraftV1,
    CitationBindingDraftV2,
    CitationBindingV1,
    ProtectedCitationEvidenceV1,
    ProtectedDeclaredEvidencePageIntegrityError,
    ProtectedDeclaredEvidencePageV1,
    ProtectedDeclaredEvidenceV1,
)
from atlas_production.modules.context_engineering.public import (
    ContextLineageEdgeV3,
    ContextLineageGraphV3,
    ContextPackV3,
    TurnInputProjectionV1,
)
from atlas_production.modules.conversation.public import ConversationTurnMemberV1, ConversationV1
from atlas_production.modules.result_governance.public import GovernedAnswerDraftV2
from atlas_production.modules.retrieval.public import DeclaredEvidenceMappingV1
from atlas_production.modules.retrieval.public import (
    ClaimedEvidenceLineageV1,
    DiscoveryCandidateComponentV1,
    DiscoveryCandidateLineageV1,
    DiscoveryChannelTraceV1,
    EvidencePackLineageItemV1,
    EvidencePackRefV1,
    RelevantDocumentDiscoveryTraceV1,
)
from atlas_production.modules.turn_runtime.public import (
    BudgetSnapshotV1,
    ExecutionLeaseV1,
    ExecutionSnapshotV1,
    ExecutionState,
    RoutePolicyV1,
    TerminalOutcomeV1,
)
from atlas_production.modules.workspace_turn.public import (
    WorkspaceTurnApplication,
    WorkspaceTurnError,
)
from tests.turn_runtime_fixtures import route_snapshot
from tests.answer_behavior_fixtures import NullAnswerBehavior


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _snapshot(turn: str, execution: str, context_ref: str) -> ExecutionSnapshotV1:
    return ExecutionSnapshotV1(
        execution_id=execution,
        turn_id=turn,
        conversation_id="conversation-1",
        actor_id="actor-1",
        state=ExecutionState.TERMINAL_COMPLETED,
        version=7,
        policy=RoutePolicyV1(),
        route=route_snapshot(),
        input_digest="0" * 64,
        response_language="zh-TW",
        applied_guidance_revision=0,
        applied_guidance_digest=None,
        lease=ExecutionLeaseV1(
            execution_id=execution,
            holder_id=f"holder-{execution}",
            lease_version=2,
            fencing_token=1,
            acquired_at=NOW,
            heartbeat_at=NOW,
            expires_at=NOW + timedelta(seconds=15),
        ),
        budget=BudgetSnapshotV1(
            tool_invocations=1,
            catalog_pages=0,
            document_candidates=1,
            search_rounds=1,
            unique_evidence=1,
            provider_invocations=2,
            context_tokens=10,
            tool_tokens=10,
        ),
        grant_ref=f"grant-{execution}",
        catalog_ref=f"catalog-{execution}",
        context_pack_ref=context_ref,
        terminal_commit_intent_ref=f"terminal-{execution}",
        deadline_at=NOW + timedelta(seconds=120),
        created_at=NOW,
        updated_at=NOW,
    )


class _Conversations:
    members = [
        ConversationTurnMemberV1(
            turn_id="turn-1", conversation_id="conversation-1", execution_id="execution-1",
            role="user", ordinal=1, created_at=NOW,
        ),
        ConversationTurnMemberV1(
            turn_id="turn-2", conversation_id="conversation-1", execution_id="execution-2",
            role="user", ordinal=2, created_at=NOW,
        ),
    ]

    def candidate_turns(self, _conversation_id):
        return self.members

    def get_turn(self, turn_id):
        return next((item for item in self.members if item.turn_id == turn_id), None)

    def get(self, conversation_id):
        if conversation_id != "conversation-1":
            return None
        return ConversationV1(
            conversation_id=conversation_id,
            owner_actor_id="actor-1",
            title="Conversation",
            status="active",
            response_language="zh-TW",
            created_at=NOW,
            updated_at=NOW,
        )

    def append_retry_turn_member(self, *, actor_id, command, retry_of_turn_id):
        raise AssertionError("retry append is not used by visibility tests")

    def retry_sources(self, _conversation_id):
        return {}


class _Contexts:
    def lineage_graph(self, _turn_ids):
        return ContextLineageGraphV3(
            candidate_turn_ids=["turn-1", "turn-2"],
            edges=[
                ContextLineageEdgeV3(
                    dependent_turn_id="turn-2",
                    dependent_context_pack_ref="context-2",
                    source_turn_id="turn-1",
                    source_resource_kind="turn",
                    dependency_kind="recent_turn",
                )
            ],
        )

    def get(self, ref):
        turn = "turn-1" if ref == "context-1" else "turn-2"
        return ContextPackV3(
            context_pack_ref=ref,
            execution_id="execution-1" if turn == "turn-1" else "execution-2",
            input_projection_ref=f"input-projection-{turn}",
            model_user_input=f"question {turn}",
            recent_tail=[],
            summary=None,
            dependencies=[],
            token_budget=100,
            digest="a" * 64,
            created_at=NOW,
        )

    def get_input_projection(self, execution_id):
        return TurnInputProjectionV1(
            projection_ref=f"projection-{execution_id}",
            execution_id=execution_id,
            original_user_input=f"question {execution_id.removeprefix('execution-')}",
            created_at=NOW,
            updated_at=NOW,
        )


class _Runtime:
    snapshots = {
        "execution-1": _snapshot("turn-1", "execution-1", "context-1"),
        "execution-2": _snapshot("turn-2", "execution-2", "context-2"),
    }

    def snapshot(self, execution_id):
        return self.snapshots[execution_id]

    def terminal_outcome(self, execution_id):
        return TerminalOutcomeV1(
            execution_id=execution_id,
            outcome="completed",
            terminal_commit_intent_ref=f"terminal-{execution_id}",
            evidence_pack_ref=f"evidence-pack-{execution_id}",
            governed_answer_draft_ref=f"answer-{execution_id}",
            citation_binding_draft_ref=f"citation-{execution_id}",
            audit_draft_ref=f"audit-{execution_id}",
            committed_at=NOW,
        )

    def events(self, execution_id):
        return [f"event-{execution_id}"]


class _Retrieval:
    def read_evidence_pack(self, ref):
        execution_id = ref.removeprefix("evidence-pack-")
        items = []
        if execution_id == "execution-1":
            items = [
                EvidencePackLineageItemV1(
                    evidence_handle="kh_evidence_one",
                    evidence_ref="evidence-1",
                    evidence_digest="b" * 64,
                    resource_ref="document-resource-1",
                    lifecycle_epoch=4,
                    document_version_ref="version-1",
                    processing_revision_ref="revision-1",
                    processing_generation_ref="processing-1",
                    index_generation_ref="index-1",
                    page_artifact_ref="page-artifact-1",
                    result_ref="result-1",
                    invocation_ordinal=1,
                )
            ]
        return EvidencePackRefV1(
            evidence_pack_ref=ref,
            execution_id=execution_id,
            catalog_ref=f"catalog-{execution_id}",
            items=items,
            digest="c" * 64,
            created_at=NOW,
        )

    def read_claimed_evidence_lineage(self, **facts):
        items = []
        for position, handle in enumerate(facts["handles"], start=1):
            duplicate_of_position = facts["handles"].index(handle) + 1
            duplicate_of_position = (
                duplicate_of_position
                if duplicate_of_position < position
                else None
            )
            if handle == "kh_evidence_one":
                items.append(
                    ClaimedEvidenceLineageV1(
                        position=position,
                        handle=handle,
                        resolution_status="resolved",
                        duplicate_of_position=duplicate_of_position,
                        handle_kind="evidence",
                        evidence_ref="evidence-1",
                        result_ref="result-1",
                        invocation_ordinal=1,
                        document_ref="document-resource-1",
                        document_handle="kh_document_one",
                        lifecycle_epoch=4,
                        document_version_ref="version-1",
                        processing_revision_ref="revision-1",
                        processing_generation_ref="processing-1",
                        index_generation_ref="index-1",
                        document_display_name="Document 1",
                        document_version_label="v1",
                        page_number=1,
                        locator_label="Page 1",
                    )
                )
            else:
                items.append(
                    ClaimedEvidenceLineageV1(
                        position=position,
                        handle=handle,
                        resolution_status="unresolved",
                        duplicate_of_position=duplicate_of_position,
                    )
                )
        return items

    def read_discovery_traces(self, **_facts):
        return []


class _Authorization:
    visible = False
    calls = 0

    def current_visibility(self, *, actor_id, resources):
        self.calls += 1
        return [
            VisibilityDecisionV1(
                resource_ref=item.resource_ref,
                decision="visible" if self.visible else "hidden",
                reason="authorized" if self.visible else "access_revoked",
            )
            for item in resources
        ]


class _Results:
    def __init__(self, audits):
        self.audits = audits

    def read_v2(self, ref):
        execution_id = ref.removeprefix("answer-")
        handles = self.audits.claims.get(execution_id, [])
        first_positions = {}
        mappings = []
        subset_positions = {}
        for position, handle in enumerate(handles, start=1):
            first_positions.setdefault(handle, position)
            if handle == "kh_evidence_one":
                subset_positions.setdefault(handle, len(subset_positions) + 1)
            subset_position = subset_positions.get(handle)
            mappings.append(
                DeclaredEvidenceMappingV1(
                    position=position,
                    handle=handle,
                    resolution_status=(
                        "resolved" if subset_position is not None else "unresolved"
                    ),
                    duplicate_of_position=(
                        None
                        if first_positions[handle] == position
                        else first_positions[handle]
                    ),
                    subset_position=subset_position,
                    reason_code=(
                        "resolved"
                        if subset_position is not None
                        else "unknown_or_out_of_execution"
                    ),
                )
            )
        return GovernedAnswerDraftV2(
            draft_ref=ref,
            execution_id=execution_id,
            retrieval_status="evidence_found" if execution_id == "execution-1" else "not_used",
            evidence_review_status="questionable",
            evidence_review_reason_codes=["assessment_not_completed"],
            declared_evidence_mappings=mappings,
            assessment_state="unavailable",
            assessment_reason_code="provider_failed",
            assessment_results=[],
            segments=[
                {
                    "segment_id": f"segment-{execution_id}",
                    "text": f"answer {execution_id}",
                }
            ],
            digest="d" * 64,
            created_at=NOW,
        )


class _Citations:
    def read(self, ref):
        execution_id = ref.removeprefix("citation-")
        return CitationBindingDraftV1(
            draft_ref=ref,
            execution_id=execution_id,
            governed_answer_draft_ref=f"answer-{execution_id}",
            governed_answer_digest="d" * 64,
            bindings=(
                [
                    CitationBindingV1(
                        citation_ref="citation-1",
                        segment_id="segment-execution-1",
                        claim_id="claim-1",
                        evidence_ref="evidence-1",
                    )
                ]
                if execution_id == "execution-1"
                else []
            ),
            digest="e" * 64,
            created_at=NOW,
        )

    def read_v2(self, ref):
        execution_id = ref.removeprefix("citation-")
        return CitationBindingDraftV2(
            draft_ref=ref,
            execution_id=execution_id,
            governed_answer_draft_ref=f"answer-{execution_id}",
            governed_answer_digest="d" * 64,
            bindings=[],
            digest="e" * 64,
            created_at=NOW,
        )


class _Audits:
    def __init__(self):
        self.claims: dict[str, list[str]] = {}

    def read_v2(self, ref):
        execution_id = ref.removeprefix("audit-")
        return TurnAuditDraftV2(
            draft_ref=ref,
            execution_id=execution_id,
            claimed_evidence_handles=self.claims.get(execution_id, []),
            evidence_pack_ref=f"evidence-pack-{execution_id}",
            evidence_pack_digest="c" * 64,
            governed_answer_draft_ref=f"answer-{execution_id}",
            governed_answer_digest="d" * 64,
            citation_binding_draft_ref=f"citation-{execution_id}",
            citation_binding_digest="e" * 64,
            retrieval_status=(
                "evidence_found" if execution_id == "execution-1" else "not_used"
            ),
            evidence_review_status="questionable",
            terminal_status="terminal_completed",
            steps=[],
            digest="f" * 64,
            created_at=NOW,
        )


class _CitationReader:
    def __init__(self):
        self.commands = []

    def read_protected(self, command):
        self.commands.append(command)
        return ProtectedCitationEvidenceV1(
            citation_ref=command.citation_ref,
            locator_label="Page 1",
            snippet="authorized",
            content="authorized evidence",
            modality="text",
        )


class _DeclaredEvidenceReader:
    def __init__(self):
        self.commands = []
        self.page = None
        self.integrity_error = False

    def read_protected_declared(
        self, command, *, accepted_page_media_types=frozenset()
    ):
        self.commands.append((command, accepted_page_media_types))
        if self.integrity_error:
            raise ProtectedDeclaredEvidencePageIntegrityError(
                "page storage failed integrity"
            )
        if self.page is not None and accepted_page_media_types:
            return self.page
        return ProtectedDeclaredEvidenceV1(
            evidence_handle=command.evidence_handle,
            locator_label="Page 1",
            snippet="authorized",
            content="authorized declared evidence",
            modality="text",
        )


def _application(authorization, citation_reader=None, declared_evidence_reader=None):
    conversations = _Conversations()
    contexts = _Contexts()
    audits = _Audits()
    return WorkspaceTurnApplication(
        conversations=conversations,
        retry_lineage=conversations,
        authorization=authorization,
        knowledge_source=SimpleNamespace(),
        contexts=contexts,
        input_projections=contexts,
        retrieval=_Retrieval(),
        generation_retention=SimpleNamespace(),
        runtime=_Runtime(),
        results=_Results(audits),
        citations=_Citations(),
        audits=audits,
        citation_reader=citation_reader or _CitationReader(),
        declared_evidence_reader=(
            declared_evidence_reader or _DeclaredEvidenceReader()
        ),
        carrier=SimpleNamespace(),
        model_routes=SimpleNamespace(),
        answer_behavior=NullAnswerBehavior(),
        context_preparer=SimpleNamespace(),
        conversation_usage=SimpleNamespace(),
    )


def test_revoke_hides_direct_turn_and_transitive_dependent_without_mask_writes() -> None:
    authorization = _Authorization()
    app = _application(authorization)

    assert app._visible_turns("actor-1", "conversation-1") == []
    assert authorization.calls == 1


def test_revocation_preserves_workspace_and_admin_detail_with_redacted_claim() -> None:
    authorization = _Authorization()
    app = _application(authorization)
    app._audits.claims["execution-1"] = [
        "kh_evidence_one",
        "kh_evidence_one",
    ]
    actor = SimpleNamespace(actor_id="actor-1", active=True)

    workspace = app.get_conversation(actor, "conversation-1")
    admin = app.audit_get_conversation(
        actor_id="admin-1", conversation_id="conversation-1"
    )

    for detail in (workspace, admin):
        assert [item.turn_id for item in detail.turns] == ["turn-1", "turn-2"]
        direct = detail.turns[0]
        assert direct.segments[0].text == "answer execution-1"
        assert direct.evidence_review_status == "questionable"
        assert direct.citations == []
        assert [
            item.resolution_status for item in direct.model_claimed_evidence
        ] == ["access_required", "access_required"]
        assert direct.model_claimed_evidence[0].handle == "kh_evidence_one"
        assert direct.model_claimed_evidence[1].duplicate_of_position == 1
        assert direct.model_claimed_evidence[0].evidence_ref is None
        assert direct.model_claimed_evidence[0].document_ref is None
        assert direct.model_claimed_evidence[0].document_display_name is None
        assert direct.model_claimed_evidence[0].locator_label is None


def test_revoked_detail_answer_and_claim_stay_out_of_next_context() -> None:
    authorization = _Authorization()
    app = _application(authorization)
    app._audits.claims["execution-1"] = ["kh_evidence_one"]

    command = app._context_command(
        snapshot=_snapshot("turn-3", "execution-3", "context-3"),
        actor_id="actor-1",
        input_text="current question",
    )
    payload = command.model_dump_json()

    assert "answer execution-1" not in payload
    assert "kh_evidence_one" not in payload
    assert "document-resource-1" not in payload
    assert any(
        item.user_message.text == "question turn-1"
        and item.assistant_message is None
        for item in command.recent_tail
    )


def test_restore_recomputes_request_projection_and_reveals_both_turns() -> None:
    authorization = _Authorization()
    app = _application(authorization)
    assert app._visible_turns("actor-1", "conversation-1") == []

    authorization.visible = True
    restored = app._visible_turns("actor-1", "conversation-1")
    assert [item.turn_id for item in restored] == ["turn-1", "turn-2"]
    assert all(
        item.evidence_review_status == "questionable" for item in restored
    )


def test_admin_runtime_fails_closed_for_direct_and_transitive_hidden_turns() -> None:
    authorization = _Authorization()
    app = _application(authorization)

    for turn_id in ("turn-1", "turn-2"):
        with pytest.raises(WorkspaceTurnError) as caught:
            app.audit_execution(
                actor_id="admin-1",
                conversation_id="conversation-1",
                turn_id=turn_id,
            )
        assert caught.value.status_code == 404
        assert caught.value.error_code == "not_found"


def test_admin_runtime_restore_recomputes_visibility_before_returning_events() -> None:
    authorization = _Authorization()
    authorization.visible = True
    app = _application(authorization)

    snapshot, events, discovery = app.audit_execution(
        actor_id="admin-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
    )

    assert snapshot.turn_id == "turn-2"
    assert events == ["event-execution-2"]
    assert discovery == []


def test_protected_citation_read_recomputes_visibility_and_uses_exact_lineage() -> None:
    authorization = _Authorization()
    reader = _CitationReader()
    app = _application(authorization, reader)
    actor = SimpleNamespace(actor_id="actor-1", active=True)

    with pytest.raises(WorkspaceTurnError) as hidden:
        app.read_citation(actor, "conversation-1", "turn-1", "citation-1")
    assert hidden.value.status_code == 404
    assert reader.commands == []

    authorization.visible = True
    result = app.read_citation(actor, "conversation-1", "turn-1", "citation-1")
    assert result.content == "authorized evidence"
    assert reader.commands[0].evidence_ref == "evidence-1"
    assert reader.commands[0].document_version_ref == "version-1"
    assert reader.commands[0].processing_revision_ref == "revision-1"
    assert reader.commands[0].index_generation_ref == "index-1"
    assert reader.commands[0].page_artifact_ref == "page-artifact-1"


def test_declared_evidence_open_requires_current_visibility_and_exact_open_ref() -> None:
    authorization = _Authorization()
    app = _application(authorization)
    app._audits.claims["execution-1"] = ["kh_evidence_one"]
    actor = SimpleNamespace(actor_id="actor-1", active=True)

    hidden = app.get_conversation(actor, "conversation-1").turns[0]
    assert hidden.model_claimed_evidence[0].protected_open_ref is None

    authorization.visible = True
    visible = app.get_conversation(actor, "conversation-1").turns[0]
    open_ref = visible.model_claimed_evidence[0].protected_open_ref
    assert open_ref is not None
    result = app.read_declared_evidence(
        actor, "conversation-1", "turn-1", open_ref
    )
    assert result.evidence_handle == "kh_evidence_one"
    assert result.content == "authorized declared evidence"
    assert app._declared_evidence_reader.commands[-1][0].declaration_position == 1

    with pytest.raises(WorkspaceTurnError) as invalid:
        app.read_declared_evidence(
            actor, "conversation-1", "turn-1", open_ref + "-changed"
        )
    assert invalid.value.status_code == 404

    authorization.visible = False
    with pytest.raises(WorkspaceTurnError) as revoked:
        app.read_declared_evidence(
            actor, "conversation-1", "turn-1", open_ref
        )
    assert revoked.value.status_code == 404


def test_declared_evidence_page_opt_in_occurs_after_current_visibility() -> None:
    authorization = _Authorization()
    reader = _DeclaredEvidenceReader()
    reader.page = ProtectedDeclaredEvidencePageV1(
        media_type="image/png",
        content=b"exact png page",
    )
    app = _application(
        authorization,
        declared_evidence_reader=reader,
    )
    app._audits.claims["execution-1"] = ["kh_evidence_one"]
    actor = SimpleNamespace(actor_id="actor-1", active=True)

    authorization.visible = True
    open_ref = (
        app.get_conversation(actor, "conversation-1")
        .turns[0]
        .model_claimed_evidence[0]
        .protected_open_ref
    )
    assert open_ref is not None
    result = app.read_declared_evidence(
        actor,
        "conversation-1",
        "turn-1",
        open_ref,
        accepted_page_media_types=frozenset({"image/png"}),
    )
    assert result == reader.page
    assert len(reader.commands) == 1

    authorization.visible = False
    with pytest.raises(WorkspaceTurnError) as revoked:
        app.read_declared_evidence(
            actor,
            "conversation-1",
            "turn-1",
            open_ref,
            accepted_page_media_types=frozenset({"image/png"}),
        )
    assert revoked.value.status_code == 404
    assert len(reader.commands) == 1


def test_declared_evidence_page_integrity_failure_uses_generic_not_found() -> None:
    authorization = _Authorization()
    authorization.visible = True
    reader = _DeclaredEvidenceReader()
    reader.integrity_error = True
    app = _application(
        authorization,
        declared_evidence_reader=reader,
    )
    app._audits.claims["execution-1"] = ["kh_evidence_one"]
    actor = SimpleNamespace(actor_id="actor-1", active=True)
    open_ref = (
        app.get_conversation(actor, "conversation-1")
        .turns[0]
        .model_claimed_evidence[0]
        .protected_open_ref
    )
    assert open_ref is not None

    with pytest.raises(WorkspaceTurnError) as failure:
        app.read_declared_evidence(
            actor,
            "conversation-1",
            "turn-1",
            open_ref,
            accepted_page_media_types=frozenset({"application/pdf"}),
        )

    assert failure.value.status_code == 404
    assert failure.value.error_code == "not_found"
    assert failure.value.message_code == "citation.was_not_found"


def test_claimed_evidence_projection_hides_metadata_without_changing_answer_state() -> None:
    authorization = _Authorization()
    app = _application(authorization)
    lineage = [
        ClaimedEvidenceLineageV1(
            position=1,
            handle="kh_evidence_declared",
            resolution_status="resolved",
            handle_kind="evidence",
            evidence_ref="evidence-1",
            result_ref="result-1",
            invocation_ordinal=1,
            document_ref="document-resource-1",
            document_handle="kh_document_declared",
            lifecycle_epoch=4,
            document_version_ref="version-1",
            processing_revision_ref="revision-1",
            processing_generation_ref="processing-1",
            index_generation_ref="index-1",
            document_display_name="Document 1",
            document_version_label="v1",
            page_number=3,
            locator_label="Page 3",
        ),
        ClaimedEvidenceLineageV1(
            position=2,
            handle="unknown-handle",
            resolution_status="unresolved",
        ),
        ClaimedEvidenceLineageV1(
            position=3,
            handle="kh_evidence_declared",
            resolution_status="resolved",
            duplicate_of_position=1,
            handle_kind="evidence",
            evidence_ref="evidence-1",
            result_ref="result-1",
            invocation_ordinal=1,
            document_ref="document-resource-1",
            document_handle="kh_document_declared",
            lifecycle_epoch=4,
            document_version_ref="version-1",
            processing_revision_ref="revision-1",
            processing_generation_ref="processing-1",
            index_generation_ref="index-1",
            document_display_name="Document 1",
            document_version_label="v1",
            page_number=3,
            locator_label="Page 3",
        ),
    ]

    evidence_pack = EvidencePackRefV1(
        evidence_pack_ref="evidence-pack-execution-1",
        execution_id="execution-1",
        catalog_ref="catalog-execution-1",
        items=[
            EvidencePackLineageItemV1(
                evidence_handle="kh_evidence_declared",
                evidence_ref="evidence-1",
                evidence_digest="b" * 64,
                resource_ref="document-resource-1",
                lifecycle_epoch=4,
                document_version_ref="version-1",
                processing_revision_ref="revision-1",
                processing_generation_ref="processing-1",
                index_generation_ref="index-1",
                result_ref="result-1",
                invocation_ordinal=1,
            )
        ],
        digest="c" * 64,
        created_at=NOW,
    )
    mappings = [
        DeclaredEvidenceMappingV1(
            position=1,
            handle="kh_evidence_declared",
            resolution_status="resolved",
            subset_position=1,
            reason_code="resolved",
        ),
        DeclaredEvidenceMappingV1(
            position=2,
            handle="unknown-handle",
            resolution_status="unresolved",
            reason_code="unknown_or_out_of_execution",
        ),
        DeclaredEvidenceMappingV1(
            position=3,
            handle="kh_evidence_declared",
            resolution_status="resolved",
            duplicate_of_position=1,
            subset_position=1,
            reason_code="resolved",
        ),
    ]

    hidden = app._project_claimed_evidence(
        "actor-1", "execution-1", lineage, evidence_pack, mappings
    )
    assert [item.resolution_status for item in hidden] == [
        "access_required",
        "unresolved",
        "access_required",
    ]
    assert hidden[0].document_ref is None
    assert hidden[2].duplicate_of_position == 1

    authorization.visible = True
    visible = app._project_claimed_evidence(
        "actor-1", "execution-1", lineage, evidence_pack, mappings
    )
    assert [item.resolution_status for item in visible] == [
        "resolved",
        "unresolved",
        "resolved",
    ]
    assert visible[0].document_ref == "document-resource-1"
    assert visible[0].locator_label == "Page 3"


def test_discovery_trace_projection_keeps_raw_order_but_hides_revoked_metadata() -> None:
    authorization = _Authorization()
    app = _application(authorization)
    trace = RelevantDocumentDiscoveryTraceV1(
        invocation_id="invocation-1",
        result_ref="result-discovery-1",
        invocation_ordinal=2,
        query_text="retention policy",
        requested_limit=20,
        ranking_contract="equal-reciprocal-rank-v1",
        channels=[
            DiscoveryChannelTraceV1(channel="lexical", status="completed"),
            DiscoveryChannelTraceV1(channel="vector", status="failed"),
        ],
        degraded=True,
        candidates=[
            DiscoveryCandidateLineageV1(
                position=1,
                document_handle="kh_document_policy",
                fused_score="1/1",
                best_component_rank=1,
                components=[
                    DiscoveryCandidateComponentV1(
                        channel="lexical",
                        rank=1,
                        match_ref="match-1",
                        locator_label="p. 4",
                        page_number=4,
                    )
                ],
                document_ref="document-policy",
                lifecycle_epoch=3,
                document_version_ref="version-policy",
                processing_revision_ref="revision-policy",
                processing_generation_ref="processing-policy",
                index_generation_ref="index-policy",
                document_display_name="Retention Policy.pdf",
                document_version_label="2026",
                preview="Keep records for seven years.",
                locator_label="p. 4",
                page_number=4,
            )
        ],
    )

    hidden = app._project_discovery_traces("actor-1", [trace])[0]
    assert hidden.result_ref == "result-discovery-1"
    assert hidden.candidates[0].position == 1
    assert hidden.candidates[0].document_handle == "kh_document_policy"
    assert hidden.candidates[0].resolution_status == "access_required"
    assert hidden.candidates[0].document_ref is None
    assert hidden.candidates[0].preview is None
    assert hidden.candidates[0].components == []

    authorization.visible = True
    visible = app._project_discovery_traces("actor-1", [trace])[0]
    assert visible.candidates[0].resolution_status == "resolved"
    assert visible.candidates[0].document_ref == "document-policy"
    assert visible.candidates[0].document_version_ref == "version-policy"
    assert visible.candidates[0].processing_revision_ref == "revision-policy"
    assert visible.candidates[0].index_generation_ref == "index-policy"
    assert visible.candidates[0].preview == "Keep records for seven years."
