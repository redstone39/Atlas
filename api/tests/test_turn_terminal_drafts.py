from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from atlas_production.infrastructure.persistence.audit_events import TURN_AUDIT_OWNER_TABLES
from atlas_production.infrastructure.persistence.base import OrmBase
from atlas_production.infrastructure.persistence.citation_preview import TURN_CITATION_OWNER_TABLES
from atlas_production.infrastructure.persistence.result_governance import (
    TURN_RESULT_GOVERNANCE_OWNER_TABLES,
)
from atlas_production.infrastructure.postgres_owner.citation_v1 import (
    PostgresCitationV1Store,
    _eligible_bindings,
)
from atlas_production.infrastructure.postgres_owner.result_governance_v1 import (
    _governed_segments,
    _governed_segments_v2,
)
from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    ResultHandleInput,
    RetrievalStoreConflict,
)
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import (
    KnowledgeToolService,
)
from atlas_production.infrastructure.strict_posthoc_claim_evaluator import (
    ClaimAssessmentUnavailable,
    StrictPostHocClaimEvaluator,
)
from atlas_production.modules.audit.public import MaterializeTurnAuditDraftV1
from atlas_production.modules.citation_preview.public import (
    MaterializeCitationBindingDraftV1,
    MaterializeCitationBindingDraftV2,
)
from atlas_production.modules.result_governance.public import (
    ExecutionEvidenceLineageV1,
    FinalizedAnswerV1,
    GovernedAnswerDraftV1,
    GovernedAnswerDraftV2,
    MaterializeGovernedAnswerDraftV1,
    MaterializeGovernedAnswerDraftV2,
    PostHocAnswerAssessmentV2,
    PostHocClaimAssessmentV1,
)
from atlas_production.modules.model_routing.public import (
    ProviderAssistantMessage,
    ProviderCompleted,
    ProviderImageContentPart,
    ProviderProtocolError,
    ProviderRefused,
    ProviderTimeoutError,
)
from atlas_production.modules.retrieval.public import (
    DeclaredEvidenceItemV1,
    DeclaredEvidenceMappingV1,
    DeclaredEvidenceSubsetV1,
    GovernanceEvidenceItemV1,
    GovernanceEvidencePackV1,
    ModelVisibleEvidenceObservationV1,
    VisualImagePayloadV1,
)
from atlas_production.providers import ProviderError
from atlas_production.modules.turn_runtime.public import (
    RoutePolicyV1,
    TurnRouteSnapshotV2,
    VisionRouteSnapshotV1,
)
from tests.test_turn_model_loop import Runtime


HANDLE = "evidence-handle-0001"


def _answer(*, mixed_same_segment: bool = False) -> FinalizedAnswerV1:
    text = "Atlas 已驗證✅。另一項未驗證。" if mixed_same_segment else "Atlas 已驗證✅。"
    return FinalizedAnswerV1(
        segments=[{"segment_id": "segment-1", "text": text}]
    )


def _lineage() -> ExecutionEvidenceLineageV1:
    return ExecutionEvidenceLineageV1(
        evidence_handle=HANDLE,
        evidence_ref="evidence-ref-1",
        evidence_digest="a" * 64,
        result_ref="retrieval-result-1",
        invocation_ordinal=1,
    )


def _assessment(
    *, start: int = 0, end: int = 10, decision: str = "supported", handle: str = HANDLE
) -> PostHocClaimAssessmentV1:
    return PostHocClaimAssessmentV1(
        segment_id="segment-1",
        start=start,
        end=end,
        decision=decision,
        supporting_evidence_handles=[handle] if decision == "supported" else [],
    )


def _command(
    *,
    finalized_answer: FinalizedAnswerV1 | None = None,
    assessments: list[PostHocClaimAssessmentV1] | None = None,
    assessment_succeeded: bool = True,
    retrieval_status: str = "evidence_found",
) -> MaterializeGovernedAnswerDraftV1:
    return MaterializeGovernedAnswerDraftV1(
        draft_ref="answer-draft-1",
        execution_id="execution-1",
        finalized_answer=finalized_answer or _answer(),
        retrieval_status=retrieval_status,
        evidence_lineage=[_lineage()] if retrieval_status == "evidence_found" else [],
        assessment_succeeded=assessment_succeeded,
        assessments=assessments if assessments is not None else [_assessment()],
        idempotency_key="governance-key-1",
    )


def _draft(command: MaterializeGovernedAnswerDraftV1) -> GovernedAnswerDraftV1:
    segments, status = _governed_segments(command)
    return GovernedAnswerDraftV1(
        draft_ref=command.draft_ref,
        execution_id=command.execution_id,
        retrieval_status=command.retrieval_status,
        verification_status=status,
        segments=segments,
        digest="b" * 64,
        created_at=datetime.now(timezone.utc),
    )


def test_global_claim_aggregation_all_some_none_and_same_segment_mixed() -> None:
    verified_segments, verified = _governed_segments(_command())
    assert verified == "verified"
    assert verified_segments[0].verification_status == "verified"
    assert verified_segments[0].claims[0].evidence_refs == ["evidence-ref-1"]

    mixed = _command(
        finalized_answer=_answer(mixed_same_segment=True),
        assessments=[
            _assessment(),
            _assessment(start=11, end=18, decision="unsupported"),
        ],
    )
    mixed_segments, partially_verified = _governed_segments(mixed)
    assert partially_verified == "partially_verified"
    assert mixed_segments[0].verification_status == "unverified"
    assert [claim.verification_status for claim in mixed_segments[0].claims] == [
        "verified",
        "unverified",
    ]

    unsupported_segments, unverified = _governed_segments(
        _command(assessments=[_assessment(decision="unsupported")])
    )
    assert unverified == "unverified"
    assert unsupported_segments[0].claims[0].evidence_refs == []


def test_zero_claims_zero_evidence_and_unsuccessful_evaluator_preserve_complete_answer() -> None:
    for command in (
        _command(assessments=[]),
        _command(
            assessments=[], retrieval_status="not_used", assessment_succeeded=True
        ),
        _command(assessments=[], assessment_succeeded=False),
    ):
        segments, status = _governed_segments(command)
        assert status == "unverified"
        assert segments[0].text == command.finalized_answer.segments[0].text
        assert segments[0].claims == []


def _v2_command(
    *,
    mappings: list[DeclaredEvidenceMappingV1] | None = None,
    lineage: list[ExecutionEvidenceLineageV1] | None = None,
    assessment_results: list[PostHocAnswerAssessmentV2] | None = None,
    assessment_state: str = "completed",
    assessment_reason_code: str = "completed",
) -> MaterializeGovernedAnswerDraftV2:
    results = (
        assessment_results
        if assessment_results is not None
        else [PostHocAnswerAssessmentV2(id="segment-1", status="success")]
    )
    consistency = (
        "insufficient"
        if any(result.status == "failure" for result in results)
        else "aligned"
    )
    return MaterializeGovernedAnswerDraftV2(
        draft_ref="answer-draft-v2",
        execution_id="execution-1",
        finalized_answer=_answer(),
        retrieval_status="evidence_found",
        declared_evidence_mappings=(
            mappings
            if mappings is not None
            else [
                DeclaredEvidenceMappingV1(
                    position=1,
                    handle=HANDLE,
                    resolution_status="resolved",
                    subset_position=1,
                    reason_code="resolved",
                )
            ]
        ),
        evidence_lineage=lineage if lineage is not None else [_lineage()],
        assessment_state=assessment_state,
        assessment_reason_code=assessment_reason_code,
        assessment_version="provisional-declared-evidence-v1",
        assessment_consistency=consistency,
        assessment_answer_digest=hashlib.sha256(
            json.dumps(
                _answer().model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        assessment_declared_subset_digest="b" * 64,
        assessment_visual_image_digests=[],
        assessment_input_digest="c" * 64,
        assessment_output_digest=(
            "d" * 64 if assessment_state == "completed" else None
        ),
        assessment_results=results,
        idempotency_key="governance-key-v2",
    )


def test_v2_aggregation_requires_all_answer_items_to_succeed() -> None:
    segments, status, reasons = _governed_segments_v2(_v2_command())
    assert status == "evidence_aligned"
    assert reasons == ["evidence_aligned"]
    assert segments[0].text == _answer().segments[0].text

    second_handle = "evidence-handle-0002"
    second = ExecutionEvidenceLineageV1(
        evidence_handle=second_handle,
        evidence_ref="evidence-ref-2",
        evidence_digest="2" * 64,
        result_ref="retrieval-result-2",
        invocation_ordinal=2,
    )
    mappings = [
        DeclaredEvidenceMappingV1(
            position=1,
            handle=HANDLE,
            resolution_status="resolved",
            subset_position=1,
            reason_code="resolved",
        ),
        DeclaredEvidenceMappingV1(
            position=2,
            handle=second_handle,
            resolution_status="resolved",
            subset_position=2,
            reason_code="resolved",
        ),
    ]
    _, unused_status, unused_reasons = _governed_segments_v2(
        _v2_command(mappings=mappings, lineage=[_lineage(), second])
    )
    assert unused_status == "evidence_aligned"
    assert unused_reasons == ["evidence_aligned"]

    _, failed_status, failed_reasons = _governed_segments_v2(
        _v2_command(
            assessment_results=[
                PostHocAnswerAssessmentV2(id="segment-1", status="failure")
            ]
        )
    )
    assert failed_status == "questionable"
    assert failed_reasons == [
        "declared_evidence_not_aligned",
        "answer_item_failed",
    ]


def test_v2_unresolved_extra_declaration_is_not_aligned() -> None:
    mappings = [
        DeclaredEvidenceMappingV1(
            position=1,
            handle=HANDLE,
            resolution_status="resolved",
            subset_position=1,
            reason_code="resolved",
        ),
        DeclaredEvidenceMappingV1(
            position=2,
            handle="kh_missing",
            resolution_status="unresolved",
            reason_code="unknown_or_out_of_execution",
        ),
    ]
    command = _v2_command(mappings=mappings).model_copy(
        update={"assessment_consistency": "insufficient"}
    )
    _, status, reasons = _governed_segments_v2(command)
    assert status == "questionable"
    assert reasons == ["declared_evidence_not_aligned"]


def test_v2_empty_or_unavailable_assessment_is_questionable_without_losing_answer() -> None:
    command = MaterializeGovernedAnswerDraftV2(
        draft_ref="answer-draft-empty-v2",
        execution_id="execution-empty-v2",
        finalized_answer=_answer(),
        retrieval_status="not_used",
        declared_evidence_mappings=[],
        evidence_lineage=[],
        assessment_state="not_attempted",
        assessment_reason_code="empty_declaration",
        assessment_version="provisional-declared-evidence-v1",
        assessment_consistency="not_applicable",
        assessment_answer_digest=hashlib.sha256(
            json.dumps(
                _answer().model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        assessment_declared_subset_digest="b" * 64,
        assessment_visual_image_digests=[],
        assessment_input_digest=None,
        assessment_output_digest=None,
        assessment_results=[],
        idempotency_key="governance-empty-v2",
    )
    segments, status, reasons = _governed_segments_v2(command)
    assert status == "questionable"
    assert reasons == [
        "empty_declaration",
        "assessment_not_completed",
        "declared_evidence_not_aligned",
    ]
    assert segments[0].text == _answer().segments[0].text


class _CitationV2Session:
    def __init__(self) -> None:
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def begin(self):
        return self

    def scalar(self, _statement):
        return self.row

    def get(self, _row_type, identity):
        if self.row is not None and self.row.draft_ref == identity:
            return self.row
        return None

    def add(self, row):
        self.row = row

    def flush(self):
        return None


def test_citation_v2_store_persists_empty_binding_without_v1_authority() -> None:
    command = _v2_command()
    segments, status, reasons = _governed_segments_v2(command)
    answer = GovernedAnswerDraftV2(
        draft_ref=command.draft_ref,
        execution_id=command.execution_id,
        retrieval_status=command.retrieval_status,
        evidence_review_status=status,
        evidence_review_reason_codes=reasons,
        declared_evidence_mappings=command.declared_evidence_mappings,
        assessment_state=command.assessment_state,
        assessment_reason_code=command.assessment_reason_code,
        assessment_version=command.assessment_version,
        assessment_consistency=command.assessment_consistency,
        assessment_answer_digest=command.assessment_answer_digest,
        assessment_declared_subset_digest=command.assessment_declared_subset_digest,
        assessment_visual_image_digests=command.assessment_visual_image_digests,
        assessment_input_digest=command.assessment_input_digest,
        assessment_output_digest=command.assessment_output_digest,
        assessment_results=command.assessment_results,
        segments=segments,
        digest="e" * 64,
        created_at=datetime.now(timezone.utc),
    )
    session = _CitationV2Session()
    store = PostgresCitationV1Store(lambda: session)

    draft = store.materialize_v2(
        MaterializeCitationBindingDraftV2(
            draft_ref="citation-draft-v2",
            execution_id=answer.execution_id,
            governed_answer=answer,
            idempotency_key="citation-key-v2",
        )
    )

    assert draft.bindings == []
    assert session.row.payload["bindings"] == []
    assert store.read(draft.draft_ref) is None
    assert store.read_v2(draft.draft_ref) == draft


class _DeclaredSubsetStore:
    def __init__(self, *, image_digest: str) -> None:
        evidence = ResultHandleInput(
            handle="kh_evidence_A",
            handle_kind="evidence",
            resource_ref="evidence-ref-A",
            evidence_identity="identity-A",
            document_handle="kh_document_A",
            source_result_ref="result-search",
            source_result_digest="1" * 64,
            source_invocation_ordinal=1,
        )
        page = ResultHandleInput(
            handle="kh_page_A",
            handle_kind="page",
            resource_ref="page|kh_document_A|1",
            document_handle="kh_document_A",
            source_result_ref="result-search",
            source_result_digest="1" * 64,
            source_invocation_ordinal=1,
        )
        visual = ResultHandleInput(
            handle="kh_visual_A",
            handle_kind="visual",
            resource_ref=f"visual|kh_document_A|1|0,0,10000,10000|{image_digest}",
            document_handle="kh_document_A",
            source_result_ref="result-visual",
            source_result_digest="3" * 64,
            source_invocation_ordinal=3,
        )
        self.resolved = {
            evidence.handle: evidence,
            page.handle: page,
            visual.handle: visual,
        }
        self.records = {
            "search_knowledge": [
                SimpleNamespace(
                    result_ref="result-search",
                    result_digest="1" * 64,
                    invocation_ordinal=1,
                    observation={
                        "result_type": "knowledge_search_result",
                        "evidence": [
                            {
                                "evidence_handle": "kh_evidence_A",
                                "document_handle": "kh_document_A",
                                "document_display_name": "Atlas",
                                "locator_label": "p.1",
                                "snippet": "model-visible snippet",
                                "modalities": ["text"],
                                "page_handle": "kh_page_A",
                                "page_number": 1,
                            }
                        ],
                        "next_cursor": None,
                    },
                )
            ],
            "inspect_knowledge": [
                SimpleNamespace(
                    result_ref="result-inspect",
                    result_digest="2" * 64,
                    invocation_ordinal=2,
                    observation={
                        "result_type": "knowledge_inspection_result",
                        "items": [
                            {
                                "evidence_handle": "kh_evidence_A",
                                "document_handle": "kh_document_A",
                                "document_display_name": "Atlas",
                                "locator_label": "p.1",
                                "content": "model-visible inspected content",
                                "modalities": ["text"],
                            }
                        ],
                    },
                )
            ],
            "expand_knowledge": [],
            "inspect_visual": [
                SimpleNamespace(
                    result_ref="result-visual",
                    result_digest="3" * 64,
                    invocation_ordinal=3,
                    observation={
                        "result_type": "visual_inspection_result",
                        "visual_handle": "kh_visual_A",
                        "source_handle": "kh_page_A",
                        "page_handle": "kh_page_A",
                        "document_handle": "kh_document_A",
                        "page_number": 1,
                        "scope": "full",
                        "bbox": {
                            "left": 0,
                            "top": 0,
                            "right": 10000,
                            "bottom": 10000,
                        },
                        "image_ref": f"image:{image_digest}",
                        "image_digest": image_digest,
                        "width": 800,
                        "height": 600,
                    },
                )
            ],
        }

    def get_catalog(self, *, execution_id, catalog_ref, deadline_at=None):
        return SimpleNamespace(execution_id=execution_id, catalog_ref=catalog_ref)

    def resolve_claimed_handles(self, *, execution_id, catalog_ref, handles):
        return tuple(self.resolved.get(handle) for handle in handles)

    def read_invocation_results(self, *, execution_id, catalog_ref, action):
        return tuple(self.records[action])


def test_declared_subset_preserves_occurrences_and_only_model_visible_content() -> None:
    image = b"same-execution-visual-carrier"
    image_digest = hashlib.sha256(image).hexdigest()
    store = _DeclaredSubsetStore(image_digest=image_digest)
    service = KnowledgeToolService(
        grant_resources=object(), store=store, backend=object()
    )
    carrier = VisualImagePayloadV1(
        visual_handle="kh_visual_A",
        image_ref=f"image:{image_digest}",
        image_digest=image_digest,
        width=800,
        height=600,
        content=image,
    )
    handles = [
        "kh_evidence_A",
        "kh_evidence_A",
        "kh_page_A",
        "kh_other_execution",
        "kh_visual_A",
    ]
    subset = service.read_declared_evidence_subset(
        execution_id="execution-1",
        catalog_ref="catalog-1",
        handles=handles,
        visual_images=[carrier],
    )

    assert [item.handle for item in subset.mappings] == handles
    assert subset.mappings[1].duplicate_of_position == 1
    assert subset.mappings[2].reason_code == "wrong_handle_kind"
    assert subset.mappings[3].reason_code == "unknown_or_out_of_execution"
    assert [item.evidence_handle for item in subset.items] == [
        "kh_evidence_A",
        "kh_visual_A",
    ]
    assert [
        observation.content_kind for observation in subset.items[0].observations
    ] == ["snippet", "content"]
    assert [
        observation.model_visible_content
        for observation in subset.items[0].observations
    ] == ["model-visible snippet", "model-visible inspected content"]
    assert subset.visual_images[0].content == image
    assert subset.visual_images[0].image_digest == image_digest

    maximum = service.read_declared_evidence_subset(
        execution_id="execution-1",
        catalog_ref="catalog-1",
        handles=["kh_evidence_A"] * 100,
        visual_images=[carrier],
    )
    assert len(maximum.mappings) == 100
    assert len(maximum.items) == 1

    with pytest.raises(RetrievalStoreConflict, match="carrier lineage changed"):
        service.read_declared_evidence_subset(
            execution_id="execution-1",
            catalog_ref="catalog-1",
            handles=["kh_visual_A"],
            visual_images=[
                carrier.model_copy(update={"image_ref": "image:wrong-ref"})
            ],
        )


@pytest.mark.parametrize(
    ("assessment", "message"),
    [
        (_assessment(handle="fabricated-handle"), "unknown evidence"),
        (_assessment(end=999), "out of bounds"),
    ],
)
def test_invalid_assessment_mapping_is_rejected(assessment, message) -> None:
    with pytest.raises(ValidationError, match=message):
        _command(assessments=[assessment])


def test_overlapping_claims_are_rejected_and_adjacent_codepoint_spans_are_valid() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        _command(
            assessments=[
                _assessment(start=0, end=10),
                _assessment(start=9, end=11, decision="unsupported"),
            ]
        )
    answer = FinalizedAnswerV1(
        segments=[{"segment_id": "segment-1", "text": "台灣✅Atlas"}]
    )
    command = _command(
        finalized_answer=answer,
        assessments=[
            _assessment(start=0, end=3),
            _assessment(start=3, end=8, decision="unsupported"),
        ],
    )
    segments, status = _governed_segments(command)
    assert status == "partially_verified"
    assert [(claim.start, claim.end) for claim in segments[0].claims] == [(0, 3), (3, 8)]


def test_server_claim_ids_are_canonical_and_verified_only_claims_bind_citations() -> None:
    answer = _draft(
        _command(
            finalized_answer=_answer(mixed_same_segment=True),
            assessments=[
                _assessment(),
                _assessment(start=11, end=18, decision="unsupported"),
            ],
        )
    )
    assert all(claim.claim_id.startswith("claim:") for claim in answer.segments[0].claims)
    bindings = _eligible_bindings(
        MaterializeCitationBindingDraftV1(
            draft_ref="citation-draft-1",
            execution_id=answer.execution_id,
            governed_answer=answer,
            idempotency_key="citation-key-1",
        )
    )
    assert [(item.claim_id, item.evidence_ref) for item in bindings] == [
        (answer.segments[0].claims[0].claim_id, "evidence-ref-1")
    ]
    assert all("url" not in type(item).model_fields for item in bindings)


def test_unused_evidence_remains_audit_only_and_never_becomes_a_citation() -> None:
    base = _command()
    unused = ExecutionEvidenceLineageV1(
        evidence_handle="evidence-handle-unused",
        evidence_ref="evidence-ref-unused",
        evidence_digest="2" * 64,
        result_ref="retrieval-result-unused",
        invocation_ordinal=2,
    )
    command = MaterializeGovernedAnswerDraftV1.model_validate(
        {
            **base.model_dump(mode="json"),
            "evidence_lineage": [
                _lineage().model_dump(mode="json"),
                unused.model_dump(mode="json"),
            ],
        }
    )
    answer = _draft(command)
    bindings = _eligible_bindings(
        MaterializeCitationBindingDraftV1(
            draft_ref="citation-draft-unused",
            execution_id=answer.execution_id,
            governed_answer=answer,
            idempotency_key="citation-key-unused",
        )
    )
    assert [binding.evidence_ref for binding in bindings] == ["evidence-ref-1"]


def test_retrieval_status_and_lineage_must_be_runtime_consistent() -> None:
    with pytest.raises(ValidationError, match="only evidence_found"):
        MaterializeGovernedAnswerDraftV1.model_validate(
            {
                **_command(
                    retrieval_status="not_used", assessments=[]
                ).model_dump(mode="json"),
                "evidence_lineage": [_lineage().model_dump(mode="json")],
            }
        )
    with pytest.raises(ValidationError, match="evidence_found requires"):
        MaterializeGovernedAnswerDraftV1(
            draft_ref="answer-draft-empty",
            execution_id="execution-empty",
            finalized_answer=_answer(),
            retrieval_status="evidence_found",
            evidence_lineage=[],
            assessment_succeeded=False,
            assessments=[],
            idempotency_key="empty-key",
        )


def test_audit_draft_rejects_raw_prompt_tool_and_provider_payload_fields() -> None:
    base = {
        "draft_ref": "audit-draft-1",
        "execution_id": "execution-1",
        "claimed_evidence_handles": ["kh_evidence_A", "kh_evidence_A"],
        "evidence_pack_ref": "evidence-pack-1",
        "evidence_pack_digest": "a" * 64,
        "governed_answer_draft_ref": "answer-draft-1",
        "governed_answer_digest": "b" * 64,
        "citation_binding_draft_ref": "citation-draft-1",
        "citation_binding_digest": "c" * 64,
        "retrieval_status": "evidence_found",
        "verification_status": "verified",
        "terminal_status": "terminal_completed",
        "steps": [],
        "idempotency_key": "audit-key-1",
    }
    MaterializeTurnAuditDraftV1.model_validate(base)
    for unsafe in ("raw_prompt", "tool_payload", "provider_payload"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            MaterializeTurnAuditDraftV1.model_validate({**base, unsafe: "secret bytes"})


def test_new_terminal_draft_foreign_keys_are_owner_local() -> None:
    for owner_tables in (
        TURN_RESULT_GOVERNANCE_OWNER_TABLES,
        TURN_CITATION_OWNER_TABLES,
        TURN_AUDIT_OWNER_TABLES,
    ):
        for table_name in owner_tables:
            table = OrmBase.metadata.tables[table_name]
            assert all(fk.column.table.name in owner_tables for fk in table.foreign_keys)


class _EvaluatorRouting:
    def __init__(
        self,
        outcome,
        *,
        max_input_tokens: int = 16000,
        tokenizer_profile: str = "cl100k_base",
        open_error: Exception | None = None,
    ):
        self.outcomes = list(outcome) if isinstance(outcome, list) else [outcome]
        self.open_error = open_error
        self.calls = 0
        self.requests = []
        self.schemas = []
        self.policy = SimpleNamespace(
            revision=1,
            tokenizer_profile=tokenizer_profile,
            context_window_tokens=128000,
            max_input_tokens_per_invocation=max_input_tokens,
            max_output_tokens_per_invocation=2000,
            max_tool_result_tokens_per_execution=16000,
            max_total_tokens_per_conversation=256000,
            provider_invocation_timeout_seconds=30,
        )
        self.opened_route_ids = []
        self.failure_codes = []
        self.successes = []

    def open_tested_attempt(self, route_id=None):
        self.opened_route_ids.append(route_id)
        if self.open_error is not None:
            raise self.open_error
        selected_route_id = route_id or "route-1"
        return SimpleNamespace(
            route=SimpleNamespace(
                route_id=selected_route_id,
                revision=1,
                supports_vision=True,
                runtime_policy=self.policy,
            ),
            provider=object(),
        )

    def invoke(self, attempt, request, schema):
        self.calls += 1
        self.requests.append(request)
        self.schemas.append(schema)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def prepare_invocation(self, *args, **kwargs):
        return object()

    def record_invocation_started(self, handle):
        return None

    def record_invocation_failure(self, handle, error_code):
        self.failure_codes.append(error_code)

    def record_invocation_success(self, handle, token_usage):
        self.successes.append(token_usage)


def _route_snapshot(routing: _EvaluatorRouting) -> TurnRouteSnapshotV2:
    return TurnRouteSnapshotV2(
        route_id="route-1",
        route_revision=1,
        runtime_policy_revision=1,
        tokenizer_profile=routing.policy.tokenizer_profile,
        context_window_tokens=routing.policy.context_window_tokens,
        max_input_tokens_per_invocation=(
            routing.policy.max_input_tokens_per_invocation
        ),
        max_output_tokens_per_invocation=(
            routing.policy.max_output_tokens_per_invocation
        ),
        max_tool_result_tokens_per_execution=(
            routing.policy.max_tool_result_tokens_per_execution
        ),
        max_total_tokens_per_conversation=(
            routing.policy.max_total_tokens_per_conversation
        ),
        vision_route=VisionRouteSnapshotV1(
            route_id="vision-route-1",
            route_revision=1,
            runtime_policy_revision=1,
            tokenizer_profile=routing.policy.tokenizer_profile,
            context_window_tokens=routing.policy.context_window_tokens,
            max_input_tokens_per_invocation=(
                routing.policy.max_input_tokens_per_invocation
            ),
            max_output_tokens_per_invocation=(
                routing.policy.max_output_tokens_per_invocation
            ),
            max_tool_result_tokens_per_execution=(
                routing.policy.max_tool_result_tokens_per_execution
            ),
            max_total_tokens_per_conversation=(
                routing.policy.max_total_tokens_per_conversation
            ),
        ),
    )


def _evaluator_pack(*, content: str = "Atlas is verified.") -> GovernanceEvidencePackV1:
    return GovernanceEvidencePackV1(
        evidence_pack_ref="evidence-pack-1",
        evidence_pack_digest="e" * 64,
        execution_id="execution-1",
        catalog_ref="catalog-1",
        items=[
            GovernanceEvidenceItemV1(
                evidence_handle=HANDLE,
                evidence_ref="evidence-ref-1",
                evidence_digest="a" * 64,
                result_ref="result-ref-1",
                invocation_ordinal=1,
                locator_label="p.1",
                snippet="Atlas",
                content=content,
                modalities=["text"],
            )
        ],
    )


def _declared_subset(
    *, content: str = "Atlas 已驗證✅。"
) -> DeclaredEvidenceSubsetV1:
    mapping = DeclaredEvidenceMappingV1(
        position=1,
        handle="kh_evidence_A",
        resolution_status="resolved",
        subset_position=1,
        reason_code="resolved",
    )
    item = DeclaredEvidenceItemV1(
        subset_position=1,
        first_declared_position=1,
        evidence_handle="kh_evidence_A",
        handle_kind="evidence",
        evidence_ref="evidence-ref-A",
        evidence_digest="a" * 64,
        source_result_ref="result-ref-A",
        source_result_digest="b" * 64,
        source_invocation_ordinal=1,
        observations=[
            ModelVisibleEvidenceObservationV1(
                result_ref="result-ref-A",
                result_digest="b" * 64,
                invocation_ordinal=1,
                result_type="knowledge_search_result",
                content_kind="snippet",
                locator_label="p.1",
                model_visible_content=content,
                modalities=["text"],
            )
        ],
    )
    return DeclaredEvidenceSubsetV1(
        execution_id="execution-1",
        catalog_ref="catalog-1",
        mappings=[mapping],
        items=[item],
        digest=hashlib.sha256(content.encode()).hexdigest(),
    )


def _completed(output: dict) -> ProviderCompleted:
    return ProviderCompleted(
        provider_request_id="provider-1",
        model_ref="model-1",
        finish_reason="stop",
        usage={"input_tokens": 10, "output_tokens": 5},
        output=output,
        assistant_message=ProviderAssistantMessage(content="{}"),
    )


def test_evaluator_uses_one_fresh_no_tool_call_and_strict_ordered_output() -> None:
    routing = _EvaluatorRouting(
        _completed(
            {
                "item_outcomes": ["aligned"]
            }
        )
    )
    result = StrictPostHocClaimEvaluator(
        routing, record_invocations=False
    ).assess(
        execution_id="execution-1",
        finalized_answer=_answer(),
        declared_evidence_subset=_declared_subset(),
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
        route=_route_snapshot(routing),
    )
    assert routing.calls == 1
    assert result.results == [
        PostHocAnswerAssessmentV2(id="segment-1", status="success")
    ]
    request = routing.requests[0]
    assert request.tools == [] and request.tool_choice == "none"
    assert request.timeout_seconds <= 20
    system_prompt = request.messages[0].content
    assert isinstance(system_prompt, str)
    assert "soft evidence-alignment assessor" in system_prompt
    assert "evaluate both evidence alignment and evidence coverage" in system_prompt
    assert "every material, externally verifiable domain claim" in system_prompt
    assert "sounds familiar, plausible, or like common knowledge" in system_prompt
    assert (
        "faithful paraphrase, summary, comparison, or direct grounded conclusion"
        in system_prompt
    )
    assert (
        "Non-material conversational framing, restating the question, or naming "
        "the adopted referent does not by itself make the item fail"
        in system_prompt
    )
    assert (
        "material fact, number, entity, relationship, causal claim" in system_prompt
    )
    assert (
        "entity, component or attribute, operating mode or interface, condition, "
        "scope or quantifier, polarity, value, and degree of certainty"
        in system_prompt
    )
    assert (
        "The same value or terminology appearing in different contexts does not "
        "establish equivalence"
        in system_prompt
    )
    assert (
        "Partial support for some members or conditions is insufficient for the "
        "broader claim"
        in system_prompt
    )
    assert (
        "A qualification, caveat, or narrower statement elsewhere in the answer "
        "does not repair an unsupported or overbroad claim"
        in system_prompt
    )
    assert "Such a grounded inference may be aligned" in system_prompt
    assert "every material premise supported" in system_prompt
    assert "does not replace an authoritative decision" in system_prompt
    assert "operational recommendations require evidence coverage" in system_prompt
    assert "A related citation does not support a conclusion" in system_prompt
    assert "A request to confirm later does not make" in system_prompt
    assert "operationalizes one side of a visible unresolved conflict" in system_prompt
    assert "other evidence-required claim with no supporting evidence" in system_prompt
    assert "evidence-backed inference from unsupported speculation" in system_prompt
    assert "private_term_gamma" not in system_prompt.lower()
    assert "oscillator" not in system_prompt.lower()
    assert "synthetic value beta" not in system_prompt.lower()
    assert "applies the evidence to the wrong subject or referent" in system_prompt
    assert "Do not require verbatim wording" in system_prompt
    assert (
        "Every factual statement in the item is directly supported"
        not in system_prompt
    )
    assert (
        "No outside knowledge or unstated inference is required"
        not in system_prompt
    )
    assessment_wire = "\n".join(
        str(message.content) for message in request.messages
    )
    assert "answer_policy_snapshot" not in assessment_wire
    assert "conversation_reply_language" not in assessment_wire
    assert "optional_custom_guidance" not in assessment_wire
    assert routing.opened_route_ids == ["route-1"]
    assert routing.schemas[0].strict is True
    assert set(routing.schemas[0].schema["properties"]) == {
        "item_outcomes",
    }
    assert "id" not in json.dumps(routing.schemas[0].schema["properties"])


def test_evaluator_invalid_output_retries_with_shared_turn_budget() -> None:
    routing = _EvaluatorRouting(
        [
            _completed({"not_item_outcomes": []}),
            _completed({"item_outcomes": ["aligned"]}),
        ]
    )
    runtime = Runtime()

    result = StrictPostHocClaimEvaluator(
        routing, runtime, record_invocations=True
    ).assess(
        execution_id="exec-1",
        finalized_answer=_answer(),
        declared_evidence_subset=_declared_subset(),
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
        route=_route_snapshot(routing),
    )

    assert result.consistency == "aligned"
    assert routing.calls == 2
    assert runtime.snapshot_value.budget.schema_retries == 1
    assert routing.failure_codes == []


def test_evaluator_schema_repair_does_not_exceed_provider_invocation_budget() -> None:
    routing = _EvaluatorRouting(
        [
            _completed({"not_item_outcomes": []}),
            _completed({"item_outcomes": ["aligned"]}),
        ]
    )
    policy = RoutePolicyV1(
        max_tool_invocations=1,
        max_reasoning_revision_cycles=0,
        max_provider_invocations=7,
    )
    runtime = Runtime(policy=policy)
    runtime.snapshot_value = runtime.snapshot_value.model_copy(
        update={
            "state": "awaiting_model_action",
            "budget": runtime.snapshot_value.budget.model_copy(
                update={"provider_invocations": 7}
            ),
        }
    )

    with pytest.raises(ClaimAssessmentUnavailable) as error:
        StrictPostHocClaimEvaluator(
            routing, runtime, record_invocations=True
        ).assess(
            execution_id="exec-1",
            finalized_answer=_answer(),
            declared_evidence_subset=_declared_subset(),
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
            route=_route_snapshot(routing),
        )

    assert error.value.reason_code == "invalid_output"
    assert routing.calls == 1
    assert runtime.snapshot_value.budget.schema_retries == 1
    assert runtime.snapshot_value.budget.provider_invocations == 7


def test_evaluator_receives_exact_visual_evidence_without_persisting_semantics() -> None:
    image = b"exact-rendered-image"
    digest = hashlib.sha256(image).hexdigest()
    subset = _declared_subset().model_copy(
        update={
            "visual_images": [
                VisualImagePayloadV1(
                    visual_handle=HANDLE,
                    image_ref=f"image:{digest}",
                    image_digest=digest,
                    width=800,
                    height=600,
                    content=image,
                )
            ]
        }
    )
    routing = _EvaluatorRouting(
        _completed({"item_outcomes": ["aligned"]})
    )

    StrictPostHocClaimEvaluator(routing, record_invocations=False).assess(
        execution_id="execution-1",
        finalized_answer=_answer(),
        declared_evidence_subset=subset,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
        route=_route_snapshot(routing),
    )
    assert routing.opened_route_ids == ["vision-route-1"]

    image_parts = [
        part
        for message in routing.requests[0].messages
        if isinstance(message.content, tuple)
        for part in message.content
        if isinstance(part, ProviderImageContentPart)
    ]
    assert len(image_parts) == 1
    assert image_parts[0].content == image
    assert image_parts[0].digest == digest
    assert "visual_images" not in subset.model_dump(mode="json")


def test_evaluator_fails_closed_before_visual_request_without_pinned_route() -> None:
    image = b"exact-rendered-image"
    digest = hashlib.sha256(image).hexdigest()
    subset = _declared_subset().model_copy(
        update={
            "visual_images": [
                VisualImagePayloadV1(
                    visual_handle=HANDLE,
                    image_ref=f"image:{digest}",
                    image_digest=digest,
                    width=800,
                    height=600,
                    content=image,
                )
            ]
        }
    )
    routing = _EvaluatorRouting(_completed({"item_outcomes": ["aligned"]}))
    route = _route_snapshot(routing).model_copy(update={"vision_route": None})

    with pytest.raises(ClaimAssessmentUnavailable) as error:
        StrictPostHocClaimEvaluator(routing, record_invocations=False).assess(
            execution_id="execution-1",
            finalized_answer=_answer(),
            declared_evidence_subset=subset,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
            route=route,
        )

    assert error.value.reason_code == "route_unavailable"
    assert routing.opened_route_ids == []
    assert routing.calls == 0


def test_evaluator_v2_payload_contains_only_declared_model_visible_subset() -> None:
    image_digest = hashlib.sha256(b"unused-visual").hexdigest()
    service = KnowledgeToolService(
        grant_resources=object(),
        store=_DeclaredSubsetStore(image_digest=image_digest),
        backend=object(),
    )
    subset = service.read_declared_evidence_subset(
        execution_id="execution-1",
        catalog_ref="catalog-1",
        handles=["kh_evidence_A"],
        visual_images=[],
    )
    routing = _EvaluatorRouting(
        _completed({"item_outcomes": ["aligned"]})
    )

    result = StrictPostHocClaimEvaluator(
        routing, record_invocations=False
    ).assess(
        execution_id="execution-1",
        finalized_answer=_answer(),
        declared_evidence_subset=subset,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
        route=_route_snapshot(routing),
    )

    payload = json.loads(routing.requests[0].messages[1].content)
    assert set(payload) == {"answer_items", "evidence_items"}
    assert payload["answer_items"] == [
        {"id": "segment-1", "text": "Atlas 已驗證✅。"}
    ]
    assert [item["id"] for item in payload["evidence_items"]] == [
        "kh_evidence_A"
    ]
    assert set(payload["evidence_items"][0]) == {"id", "content"}
    assert result.assessment_input_digest == hashlib.sha256(
        json.dumps(
            {"payload": payload, "visual_image_digests": []},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    assert len(result.assessment_output_digest) == 64


def test_evaluator_runtime_maps_ordered_outcomes_to_exact_answer_ids() -> None:
    answer = FinalizedAnswerV1(
        segments=[
            {"segment_id": "segment-1", "text": "First."},
            {"segment_id": "segment-2", "text": "Second."},
        ]
    )
    routing = _EvaluatorRouting(
        _completed(
            {"item_outcomes": ["insufficient", "aligned"]}
        )
    )

    result = StrictPostHocClaimEvaluator(
        routing, record_invocations=False
    ).assess(
        execution_id="execution-1",
        finalized_answer=answer,
        declared_evidence_subset=_declared_subset(),
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
        route=_route_snapshot(routing),
    )

    assert result.results == [
        PostHocAnswerAssessmentV2(id="segment-1", status="failure"),
        PostHocAnswerAssessmentV2(id="segment-2", status="success"),
    ]
    assert result.consistency == "insufficient"


@pytest.mark.parametrize("item_outcomes", [[], ["aligned"], ["aligned"] * 3])
def test_evaluator_rejects_ordered_outcome_count_mismatch(item_outcomes) -> None:
    answer = FinalizedAnswerV1(
        segments=[
            {"segment_id": "segment-1", "text": "First."},
            {"segment_id": "segment-2", "text": "Second."},
        ]
    )
    routing = _EvaluatorRouting(_completed({"item_outcomes": item_outcomes}))

    with pytest.raises(ClaimAssessmentUnavailable) as error:
        StrictPostHocClaimEvaluator(
            routing, record_invocations=True
        ).assess(
            execution_id="execution-1",
            finalized_answer=answer,
            declared_evidence_subset=_declared_subset(),
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
            route=_route_snapshot(routing),
        )

    assert error.value.reason_code == "invalid_output"
    assert routing.calls == 1
    assert routing.failure_codes == []


@pytest.mark.parametrize(
    ("item_outcomes", "expected_consistency", "expected_statuses"),
    [
        (["aligned", "aligned"], "aligned", ["success", "success"]),
        (["aligned", "insufficient"], "insufficient", ["success", "failure"]),
        (["insufficient", "conflict"], "conflict", ["failure", "failure"]),
    ],
)
def test_evaluator_runtime_derives_consistency_from_ordered_item_outcomes(
    item_outcomes, expected_consistency, expected_statuses
) -> None:
    answer = FinalizedAnswerV1(
        segments=[
            {"segment_id": "segment-1", "text": "First."},
            {"segment_id": "segment-2", "text": "Second."},
        ]
    )
    routing = _EvaluatorRouting(_completed({"item_outcomes": item_outcomes}))

    result = StrictPostHocClaimEvaluator(routing, record_invocations=True).assess(
        execution_id="execution-1",
        finalized_answer=answer,
        declared_evidence_subset=_declared_subset(),
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
        route=_route_snapshot(routing),
    )

    assert result.consistency == expected_consistency
    assert [item.status for item in result.results] == expected_statuses
    assert routing.failure_codes == []
    assert len(routing.successes) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        ProviderRefused(
            provider_request_id="provider-1",
            model_ref="model-1",
            finish_reason="stop",
            usage={},
            reason_code="policy",
            message_code=None,
        ),
        _completed({"not_item_outcomes": []}),
        ProviderTimeoutError(safe_code="provider_timeout"),
    ],
)
def test_evaluator_refusal_invalid_json_and_timeout_do_not_retry(outcome) -> None:
    routing = _EvaluatorRouting(outcome)
    with pytest.raises(ClaimAssessmentUnavailable):
        StrictPostHocClaimEvaluator(routing, record_invocations=False).assess(
            execution_id="execution-1",
            finalized_answer=_answer(),
            declared_evidence_subset=_declared_subset(),
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
            route=_route_snapshot(routing),
        )
    assert routing.calls == 1


def test_evaluator_oversized_input_is_rejected_before_provider_and_internal_conflict_propagates() -> None:
    oversized = _EvaluatorRouting(
        _completed({"item_outcomes": ["aligned"]}),
        max_input_tokens=1,
    )
    with pytest.raises(ClaimAssessmentUnavailable) as limit:
        StrictPostHocClaimEvaluator(oversized, record_invocations=False).assess(
            execution_id="execution-1",
            finalized_answer=_answer(),
            declared_evidence_subset=_declared_subset(content="x" * 12000),
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
            route=_route_snapshot(oversized),
        )
    assert limit.value.reason_code == "physical_limit_rejected"
    assert oversized.calls == 0

    unavailable_route = _EvaluatorRouting(
        _completed({"item_outcomes": ["aligned"]}),
        open_error=ProviderError("model_route_unavailable", "model.route_is_unavailable"),
    )
    with pytest.raises(ClaimAssessmentUnavailable, match="route is unavailable"):
        StrictPostHocClaimEvaluator(
            unavailable_route, record_invocations=False
        ).assess(
            execution_id="execution-1",
            finalized_answer=_answer(),
            declared_evidence_subset=_declared_subset(),
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
            route=_route_snapshot(unavailable_route),
        )
    assert unavailable_route.calls == 0

    invalid_tokenizer = _EvaluatorRouting(
        _completed({"item_outcomes": ["aligned"]}),
        tokenizer_profile="not-a-tokenizer",
    )
    with pytest.raises(ClaimAssessmentUnavailable, match="tokenizer is unavailable"):
        StrictPostHocClaimEvaluator(
            invalid_tokenizer, record_invocations=False
        ).assess(
            execution_id="execution-1",
            finalized_answer=_answer(),
            declared_evidence_subset=_declared_subset(),
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
            route=_route_snapshot(invalid_tokenizer),
        )
    assert invalid_tokenizer.calls == 0

    conflict = _EvaluatorRouting(RuntimeError("routing durable conflict"))
    with pytest.raises(RuntimeError, match="durable conflict"):
        StrictPostHocClaimEvaluator(conflict, record_invocations=False).assess(
            execution_id="execution-1",
            finalized_answer=_answer(),
            declared_evidence_subset=_declared_subset(),
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
            route=_route_snapshot(conflict),
        )
    assert conflict.calls == 1
