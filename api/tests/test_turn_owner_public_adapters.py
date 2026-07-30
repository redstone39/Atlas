from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import atlas_production.infrastructure.postgres_conversation_v1_adapter as conversation_adapter
from atlas_production.infrastructure.postgres_context_engineering_v3_adapter import (
    PostgresContextEngineeringV3Adapter,
)
from atlas_production.infrastructure.postgres_owner.context_engineering import (
    ContextMessageInput,
    ContextPackRecord,
    LineageEdgeRecord,
    LineageGraphRecord,
    RecentExchangeInput,
    SummaryRecord,
    SummarySourceInput,
)
from atlas_production.infrastructure.postgres_owner.conversation_v1 import (
    ConversationRecord,
    ConversationStoreConflict,
    TurnMemberRecord,
)
from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY,
    CatalogDocumentInput,
    CatalogRecord,
    EvidencePackLineageInput,
    EvidencePackRecord,
    ResultHandleInput,
)
from atlas_production.infrastructure.postgres_owner.turn_runtime import (
    PostgresTurnRuntimeOwner,
)
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    KnowledgeToolService,
)
from atlas_production.modules.conversation.public import (
    AppendTurnMemberV1,
    ConversationCreateV1,
    ConversationTurnMemberV1,
)
from atlas_production.modules.turn_runtime.public import TerminalOutcomeV1


NOW = datetime.now(timezone.utc)


class _ConversationStore:
    current: "_ConversationStore"

    def __init__(self, _session_factory) -> None:
        type(self).current = self
        self.conversation: ConversationRecord | None = None
        self.member: TurnMemberRecord | None = None
        self.create_request = None
        self.append_request = None

    def create(self, command):
        if self.create_request is not None:
            if command != self.create_request:
                raise ConversationStoreConflict("conversation create replay payload changed")
            assert self.conversation is not None
            return self.conversation
        self.create_request = command
        self.conversation = ConversationRecord(
            command.conversation_id,
            command.actor_id,
            command.title,
            "active",
            command.response_language,
            1,
            NOW,
            NOW,
        )
        return self.conversation

    def append_turn_member(self, command):
        if self.append_request is not None:
            if command != self.append_request:
                raise ConversationStoreConflict("turn membership replay payload changed")
            assert self.member is not None
            return self.member
        self.append_request = command
        self.member = TurnMemberRecord(
            command.turn_id,
            command.conversation_id,
            command.execution_id,
            command.role,
            command.expected_next_ordinal,
            NOW,
        )
        assert self.conversation is not None
        self.conversation = replace(
            self.conversation, next_ordinal=command.expected_next_ordinal + 1
        )
        return self.member

    def get(self, conversation_id):
        if self.conversation is None or self.conversation.conversation_id != conversation_id:
            return None
        return self.conversation

    def get_turn(self, turn_id):
        if self.member is None or self.member.turn_id != turn_id:
            return None
        return self.member

    def list_for_actor(self, actor_id):
        return () if self.conversation is None else (self.conversation,)

    def candidate_turns(self, conversation_id):
        return () if self.member is None else (self.member,)

    def retry_sources(self, conversation_id):
        if self.append_request is None or self.append_request.retry_of_turn_id is None:
            return {}
        return {self.append_request.turn_id: self.append_request.retry_of_turn_id}


class _ConversationSession:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalar(self, _statement):
        member = _ConversationStore.current.member
        return (
            None
            if member is None
            else SimpleNamespace(
                turn_id=member.turn_id,
                conversation_id=member.conversation_id,
                execution_id=member.execution_id,
                role=member.role,
                ordinal=member.ordinal,
                created_at=member.created_at,
            )
        )


def test_conversation_public_adapter_hides_ordinal_and_replays_exact_identity(monkeypatch) -> None:
    monkeypatch.setattr(conversation_adapter, "PostgresConversationV1Store", _ConversationStore)
    adapter = conversation_adapter.PostgresConversationV1Adapter(_ConversationSession)
    command = ConversationCreateV1(title="A conversation", idempotency_key="create-key")
    created = adapter.create(actor_id="actor-1", command=command)
    assert created == adapter.create(
        actor_id="actor-1", command=command
    )
    assert created.response_language == "zh-TW"

    append = AppendTurnMemberV1(
        conversation_id=_ConversationStore.current.conversation.conversation_id,
        turn_id="turn-1",
        execution_id="execution-1",
        role="user",
        idempotency_key="turn-key",
    )
    first = adapter.append_turn_member(actor_id="actor-1", command=append)
    assert adapter.append_turn_member(actor_id="actor-1", command=append) == first
    assert first.ordinal == 1
    assert adapter.get(first.conversation_id).conversation_id == first.conversation_id  # type: ignore[union-attr]
    assert adapter.get_turn(first.turn_id) == first
    assert adapter.list_for_actor("actor-1")[0].conversation_id == first.conversation_id
    assert adapter.candidate_turns(first.conversation_id) == [first]
    assert "ordinal" not in AppendTurnMemberV1.model_fields
    assert "retry_of_turn_id" not in AppendTurnMemberV1.model_fields
    assert "retry_of_turn_id" not in ConversationTurnMemberV1.model_fields

    english_adapter = conversation_adapter.PostgresConversationV1Adapter(
        _ConversationSession
    )
    english = english_adapter.create(
        actor_id="actor-1",
        command=ConversationCreateV1(
            title="English",
            idempotency_key="create-en",
            response_language="en",
        ),
    )
    assert english.response_language == "en"
    assert english_adapter.get(english.conversation_id) == english
    assert english_adapter.list_for_actor("actor-1") == [english]


class _ContextStore:
    def __init__(self, pack: ContextPackRecord, graph: LineageGraphRecord) -> None:
        self.pack = pack
        self.graph = graph

    def get(self, context_pack_ref):
        return self.pack if context_pack_ref == self.pack.context_pack_ref else None

    def lineage_graph(self, _turn_ids):
        return self.graph


def test_context_read_adapter_returns_strict_public_models() -> None:
    edge = LineageEdgeRecord(
        "turn-2", "context-pack-1", "turn-1", None, "turn", "recent_turn",
        None, None, None,
    )
    pack = ContextPackRecord(
        "context-pack-1", "context-pack-v3", "execution-1",
        "input-projection-1", "conversation-1",
        "question",
        (
            RecentExchangeInput(
                "root-1",
                "turn-1",
                "c" * 64,
                ContextMessageInput("user", "question"),
                ContextMessageInput("assistant", "answer", "verified"),
            ),
        ),
        SummaryRecord(
            "summary-1",
            None,
            "older",
            1,
            (SummarySourceInput("root-0", "turn-0", "d" * 64),),
            "a" * 64,
        ),
        (edge,), 1024, "b" * 64, NOW,
    )
    adapter = PostgresContextEngineeringV3Adapter(
        _ContextStore(pack, LineageGraphRecord(("turn-2",), (edge,)))  # type: ignore[arg-type]
    )
    public = adapter.get("context-pack-1")
    assert public is not None
    assert public.recent_tail[0].representative_turn_id == "turn-1"
    assert adapter.lineage_graph(["turn-2"]).edges[0].source_turn_id == "turn-1"


class _RetrievalReadStore:
    def __init__(self) -> None:
        descriptor = {
            "display_name": "Visible.pdf",
            "media_type": "application/pdf",
            "modalities": ["text"],
            "tags": [],
            "version_label": "1",
            AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY: "must-never-be-model-visible",
        }
        self.document = CatalogDocumentInput(
            document_handle="document-handle-1", lifecycle_epoch=4,
            document_version_ref="document-version-1", generation_ref="index-generation-1",
            processing_generation_ref="processing-generation-1",
            processing_revision_ref="processing-revision-1",
            index_generation_ref="index-generation-1", manifest_digest="a" * 64,
            descriptor=descriptor, resource_ref="resource-1",
        )
        self.catalog = CatalogRecord(
            "catalog-1", "execution-1", "grant-1", "retention-1", 1,
            "knowledge-catalog-snapshot-v1", "retrieval-generation-1",
            (self.document,), "b" * 64, NOW,
        )
        self.pack = EvidencePackRecord(
            "evidence-pack-1", "execution-1", "catalog-1",
            (EvidencePackLineageInput(
                evidence_handle="evidence-handle-1",
                evidence_ref="evidence-1",
                evidence_digest="c" * 64,
                resource_ref="resource-1",
                document_version_ref="document-version-1",
                processing_revision_ref="processing-revision-1",
                index_generation_ref="index-generation-1",
                page_artifact_ref=None,
                result_ref="result-1",
                invocation_ordinal=1,
            ),), "d" * 64, NOW,
        )

    def read_evidence_pack(self, ref):
        return self.pack if ref == self.pack.evidence_pack_ref else None

    def get_catalog(self, **_kwargs):
        return self.catalog

    def resolve_handles(self, **_kwargs):
        return (ResultHandleInput(
            "evidence-handle-1", "evidence", "evidence-1",
            evidence_identity="identity-1", document_handle="document-handle-1",
            source_result_ref="result-1", source_result_digest="e" * 64,
            source_invocation_ordinal=1,
        ),)


def test_evidence_pack_read_enriches_authorization_lineage_without_model_id_leak() -> None:
    store = _RetrievalReadStore()
    service = KnowledgeToolService(grant_resources=object(), store=store, backend=object())  # type: ignore[arg-type]
    pack = service.read_evidence_pack("evidence-pack-1")
    assert pack is not None
    assert pack.items[0].model_dump()["resource_ref"] == "resource-1"
    public_descriptor = service._public_descriptor(store.document).model_dump()
    backend_descriptor = service._backend_documents(store.catalog)[0].descriptor
    assert AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY not in public_descriptor
    assert AUTHORIZATION_RESOURCE_REF_DESCRIPTOR_KEY not in backend_descriptor
    assert "resource-1" not in str(public_descriptor)


class _RuntimeSession:
    def __init__(self, outcome, intent=None) -> None:
        self.outcome = outcome
        self.intent = intent

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, model, _key):
        return self.outcome if model.__name__.endswith("TerminalOutcomeRow") else self.intent


def test_terminal_outcome_read_exposes_completed_refs_or_failed_code() -> None:
    completed = SimpleNamespace(
        execution_id="execution-1", outcome="completed",
        terminal_intent_ref="intent-1", failure_code=None, committed_at=NOW,
    )
    intent = SimpleNamespace(
        execution_id="execution-1", evidence_pack_ref="pack-1",
        governed_answer_draft_ref="answer-1", citation_binding_draft_ref="citation-1",
        audit_draft_ref="audit-1",
    )
    owner = PostgresTurnRuntimeOwner(lambda: _RuntimeSession(completed, intent))
    assert owner.terminal_outcome("execution-1").evidence_pack_ref == "pack-1"  # type: ignore[union-attr]

    failed = SimpleNamespace(
        execution_id="execution-2", outcome="failed",
        terminal_intent_ref=None, failure_code="carrier_lost", committed_at=NOW,
    )
    owner = PostgresTurnRuntimeOwner(lambda: _RuntimeSession(failed))
    assert owner.terminal_outcome("execution-2").failure_code == "carrier_lost"  # type: ignore[union-attr]
    with pytest.raises(ValidationError):
        TerminalOutcomeV1(
            execution_id="execution-3", outcome="completed", failure_code="bad", committed_at=NOW
        )
