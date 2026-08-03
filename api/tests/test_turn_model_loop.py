from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest
import tiktoken

from atlas_production.infrastructure.strict_turn_model_adapter import StrictProviderTurnModel
from atlas_production.infrastructure.strict_posthoc_claim_evaluator import (
    ClaimAssessmentUnavailable,
)
from atlas_production.infrastructure.turn_model_input_adapter import (
    PublicOwnerTurnModelInputSource,
)
from atlas_production.infrastructure.turn_capability_projection import (
    project_turn_model_capabilities,
)
from atlas_production.infrastructure.turn_execution_orchestrator import (
    StatelessTurnExecutionOrchestrator,
    _context_token_reservation,
    _has_legal_tool,
)
from atlas_production.modules.audit.public import TurnAuditDraftV2
from atlas_production.modules.citation_preview.public import CitationBindingDraftV2
from atlas_production.modules.model_routing.public import (
    ProviderAssistantMessage,
    ProviderAssistantToolCallMessage,
    ProviderCompleted,
    ProviderFunctionCall,
    ProviderSystemMessage,
    ProviderToolCall,
    ProviderUserMessage,
)
from atlas_production.modules.result_governance.public import (
    GovernedAnswerDraftV2,
    GovernedAnswerSegmentV2,
    PostHocAnswerAssessmentResultV2,
    PostHocAnswerAssessmentV2,
)
from atlas_production.modules.retrieval.public import (
    DeclaredEvidenceItemV1,
    DeclaredEvidenceMappingV1,
    DeclaredEvidenceSubsetV1,
    EvidenceDescriptorV1,
    EvidencePackLineageItemV1,
    EvidencePackRefV1,
    GovernanceEvidenceItemV1,
    GovernanceEvidencePackV1,
    KnowledgeCatalogPageV1,
    KnowledgeDocumentDescriptorV1,
    KnowledgeExpansionResultV1,
    KnowledgeInspectionItemV1,
    KnowledgeInspectionResultV1,
    KnowledgeSearchResultV1,
    ModelVisibleEvidenceObservationV1,
    RelevantDocumentCandidateV1,
    RelevantDocumentDiscoveryResultV1,
    RetrievalEvidenceLineageV1,
    RetrievalInvocationEnvelopeV1,
    VisualImagePayloadV1,
    VisualInspectionResultV1,
)
from atlas_production.modules.turn_execution.public import (
    AnswerBehaviorInputV1,
    DeepReasoningContractError,
    DeepReasoningEvaluationResultV1,
    DeepReasoningPlanResultV1,
    FinalizeAnswerV1,
    ModelActionResultV1,
    ModelContractViolationV1,
    TurnModelInputV3,
)
from atlas_production.modules.turn_runtime.public import (
    BudgetSnapshotV1,
    ExecutionLeaseV1,
    ExecutionSnapshotV1,
    ExecutionState,
    ProcessScoreV1,
    ReasoningEvaluationV1,
    ReasoningPlanItemV2,
    ReasoningPlanV2,
    RoutePolicyV1,
)
from tests.turn_runtime_fixtures import route_snapshot
from tests.answer_behavior_fixtures import NullAnswerBehavior


NOW = datetime.now(timezone.utc)
DIGEST = "a" * 64


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _budget(**changes) -> BudgetSnapshotV1:
    values = dict(
        tool_invocations=0, catalog_pages=0, document_candidates=0,
        search_rounds=0, unique_evidence=0, provider_invocations=0,
        context_tokens=0, tool_tokens=0,
    )
    values.update(changes)
    return BudgetSnapshotV1(**values)


class Runtime:
    def __init__(
        self,
        *,
        policy: RoutePolicyV1 | None = None,
        reasoning_mode: str = "standard",
    ) -> None:
        self.calls: list[str] = []
        self.model_action_repairs: list[bool] = []
        self.reservations = []
        self.document_candidate_handles: set[str] = set()
        self.reasoning_events = []
        self.snapshot_value = ExecutionSnapshotV1(
            execution_id="exec-1", turn_id="turn-1", conversation_id="conversation-1",
            actor_id="actor-1", state="context_ready", version=3,
            policy=policy or RoutePolicyV1(),
            route=route_snapshot(),
            input_digest="0" * 64,
            response_language="zh-TW",
            reasoning_mode=reasoning_mode,
            applied_guidance_revision=0,
            applied_guidance_digest=None,
            lease=ExecutionLeaseV1(
                execution_id="exec-1", holder_id="worker-1", lease_version=1,
                fencing_token=7, acquired_at=NOW, heartbeat_at=NOW,
                expires_at=NOW + timedelta(minutes=5),
            ),
            budget=_budget(), grant_ref="grant-1", catalog_ref="catalog-1",
            context_pack_ref="context-1", deadline_at=NOW + timedelta(minutes=2),
            created_at=NOW, updated_at=NOW,
        )

    def snapshot(self, execution_id):
        assert execution_id == "exec-1"
        return self.snapshot_value

    def _move(self, state, *, budget=None, terminal_ref=None):
        self.snapshot_value = self.snapshot_value.model_copy(update={
            "state": ExecutionState(state), "version": self.snapshot_value.version + 1,
            "budget": budget or self.snapshot_value.budget,
            "terminal_commit_intent_ref": terminal_ref,
        })
        return self.snapshot_value

    def request_model_action(self, command):
        self.calls.append("request_model_action")
        self.model_action_repairs.append(command.contract_repair)
        if command.contract_repair:
            assert self.snapshot_value.state is ExecutionState.AWAITING_MODEL_ACTION
        else:
            assert self.snapshot_value.state in {
                ExecutionState.CONTEXT_READY,
                ExecutionState.TOOL_COMPLETED,
                ExecutionState.AWAITING_MODEL_ACTION,
            }
        if (
            command.context_tokens
            > self.snapshot_value.policy.context_token_budget
        ):
            raise ValueError("per-invocation context token budget exceeded")
        b = self.snapshot_value.budget.model_copy(update={
            "provider_invocations": self.snapshot_value.budget.provider_invocations + 1,
            "context_tokens": self.snapshot_value.budget.context_tokens + command.context_tokens,
        })
        return self._move("awaiting_model_action", budget=b)

    def record_reasoning_progress(self, command):
        self.calls.append(f"reasoning:{command.phase}:{command.progress_status}")
        self.reasoning_events.append(command)
        self.snapshot_value = self.snapshot_value.model_copy(
            update={
                "version": self.snapshot_value.version + 1,
                "reasoning_trace": command.trace,
            }
        )
        return self.snapshot_value

    def begin_tool(self, command):
        self.calls.append(f"begin:{command.tool_name}")
        self.last_reservation = command
        self.reservations.append(command)
        b = self.snapshot_value.budget.model_copy(update={
            "tool_invocations": self.snapshot_value.budget.tool_invocations + 1,
        })
        return self._move("tool_pending", budget=b)

    def complete_tool(self, command):
        self.calls.append(f"complete:{command.invocation_ordinal}")
        new_document_candidates = set(command.document_candidate_handles).difference(
            self.document_candidate_handles
        )
        self.document_candidate_handles.update(command.document_candidate_handles)
        b = self.snapshot_value.budget.model_copy(update={
            "catalog_pages": self.snapshot_value.budget.catalog_pages + command.catalog_pages,
            "search_rounds": self.snapshot_value.budget.search_rounds + command.search_rounds,
            "document_candidates": (
                self.snapshot_value.budget.document_candidates
                + len(new_document_candidates)
            ),
            "unique_evidence": min(
                self.snapshot_value.policy.max_unique_evidence,
                self.snapshot_value.budget.unique_evidence + len(command.unique_evidence_identities),
            ),
            "tool_tokens": self.snapshot_value.budget.tool_tokens + command.tool_tokens,
        })
        self.last_unique_identities = command.unique_evidence_identities
        return self._move("tool_completed", budget=b)

    def begin_governance(self, command):
        self.calls.append("begin_governance")
        return self._move("governing_result")

    def prepare_terminal(self, command):
        self.calls.append("prepare_terminal")
        assert command.evidence_pack_ref == "evidence-pack-1"
        assert command.governed_answer_draft_ref.startswith("governed-answer-draft:")
        assert command.citation_binding_draft_ref.startswith("citation-binding-draft:")
        assert command.audit_draft_ref.startswith("turn-audit-draft:")
        return self._move("materializing_terminal", terminal_ref="terminal-intent-1")

    def commit_terminal(self, command):
        self.calls.append("commit_terminal")
        assert command.terminal_commit_intent_ref == "terminal-intent-1"
        return self._move("terminal_completed", terminal_ref="terminal-intent-1")

    def fail_carrier(self, command):
        self.calls.append(f"fail:{command.failure_code}")
        return self._move("terminal_failed")


class Inputs:
    def build(self, snapshot, *, observations=(), contract_repair_remaining=1):
        return TurnModelInputV3(
            execution_id=snapshot.execution_id, model_user_input="compare policy documents",
            recent_tail=[], summary=None,
            context_pack_ref=snapshot.context_pack_ref,
            knowledge_catalog_ref=snapshot.catalog_ref, catalog_document_count=2,
            budget=snapshot.budget, policy=snapshot.policy, route=snapshot.route,
            answer_behavior=AnswerBehaviorInputV1(
                response_language=snapshot.response_language,
                applied_guidance_revision=snapshot.applied_guidance_revision,
                applied_guidance_digest=snapshot.applied_guidance_digest,
                custom_guidance=None,
            ),
            capabilities=project_turn_model_capabilities(
                snapshot,
                catalog_document_count=2,
                observations=observations,
                contract_repair_remaining=contract_repair_remaining,
            ),
            previous_observation=(observations[-1] if observations else None),
        )


class ScriptSession:
    def __init__(self, actions, *, fail=False):
        self.actions = list(actions)
        self.observations = []
        self.finalize_only_values = []
        self.fail = fail
        self.discarded = False
        self.visual_images = []
        self.reasoning_feedback = []

    def next_action(self, model_input, *, finalize_only):
        self.finalize_only_values.append(finalize_only)
        if self.fail:
            raise RuntimeError("provider unavailable")
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        if isinstance(action, ModelContractViolationV1):
            return action
        return ModelActionResultV1(action=action)

    def estimate_next_request_tokens(self, model_input, *, finalize_only):
        content = json.dumps(
            {"turn_model_input": model_input.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return len(tiktoken.get_encoding("cl100k_base").encode(content))

    def accept_tool_observation(self, observation, *, visual_image=None):
        self.observations.append(observation.model_dump(mode="json"))
        if visual_image is not None:
            self.visual_images.append(visual_image)

    def accept_contract_repair(self, violation):
        self.observations.append(violation.model_dump(mode="json"))

    def accept_reasoning_feedback(self, evaluation, *, plan=None):
        self.reasoning_feedback.append((evaluation, plan))

    def accept_reasoning_limit(self, evaluation):
        self.reasoning_feedback.append((evaluation, "limit"))

    def discard(self):
        self.discarded = True


class ScriptModel:
    def __init__(self, actions, *, fail=False):
        self.session = ScriptSession(actions, fail=fail)

    def open_session(self, model_input):
        return self.session


class ScriptReasoningModel:
    def __init__(
        self,
        *,
        evaluations=(),
        planner_failures=0,
        replanner_failures=0,
    ):
        self.plan_calls = []
        self.evaluation_calls = []
        self.evaluations = list(evaluations)
        self.planner_failures = planner_failures
        self.replanner_failures = replanner_failures
        self.replan_calls = []

    def estimate_plan_request_tokens(self, model_input, *, repair):
        return 10

    def plan(self, model_input, *, repair):
        self.plan_calls.append(repair)
        if len(self.plan_calls) <= self.planner_failures:
            raise DeepReasoningContractError("deep_reasoning_plan_invalid")
        return DeepReasoningPlanResultV1(
            plan=ReasoningPlanV2(
                generation=1,
                next_objective="Review the request and evidence.",
                completion_condition="A supported candidate is ready.",
                items=[
                    ReasoningPlanItemV2(
                        item_id="plan-1", summary="Review the request and evidence."
                    )
                ]
            ),
            input_tokens=10,
            output_tokens=5,
        )

    def estimate_evaluation_request_tokens(self, *args, **kwargs):
        return 10

    def estimate_replan_request_tokens(self, *args, **kwargs):
        return 10

    def replan(self, model_input, *, plan, evaluation, repair):
        self.replan_calls.append(repair)
        if len(self.replan_calls) <= self.replanner_failures:
            raise DeepReasoningContractError("deep_reasoning_replan_invalid")
        return DeepReasoningPlanResultV1(
            plan=ReasoningPlanV2(
                generation=plan.generation + 1,
                parent_generation=plan.generation,
                next_objective="Close the evidence gap.",
                completion_condition="The requested evidence is available or disclosed as missing.",
                items=[item.model_copy() for item in plan.items],
            ),
            input_tokens=10,
            output_tokens=5,
        )

    def evaluate(self, model_input, **kwargs):
        self.evaluation_calls.append(kwargs["cycle"])
        outcome = self.evaluations.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return DeepReasoningEvaluationResultV1(
            evaluation=outcome,
            input_tokens=10,
            output_tokens=5,
        )


class Retrieval:
    def __init__(self):
        self.backend_calls = []
        self.invocations = []
        self.results = {}
        self.evidence = {}
        self.docs = {"kh_document_A", "kh_document_B"}
        self.materialized = False
        self.pack = None
        self.governance_reads = 0
        self.page_handles = {"kh_page_A", "kh_page_B"}
        self.visual_bboxes = {}

    def invoke(
        self,
        *,
        execution_id,
        grant_ref,
        catalog_ref,
        invocation_ordinal,
        action,
        max_output_tokens=None,
        tokenizer_profile=None,
        max_output_bytes=262_144,
    ):
        assert max_output_tokens is not None
        assert tokenizer_profile == "cl100k_base"
        handles = (
            getattr(action, "document_handles", None)
            or getattr(action, "handles", None)
            or getattr(action, "anchor_handles", None)
            or ([action.handle] if hasattr(action, "handle") else None)
            or []
        )
        expected = (
            self.docs
            if action.action == "search_knowledge"
            else (
                self.page_handles | set(self.visual_bboxes)
                if action.action == "inspect_visual"
                else set(self.evidence)
            )
        )
        if any(handle not in expected for handle in handles):
            raise ValueError("invalid handle")
        key = _hash(action.model_dump(mode="json"))
        replayed = key in self.results
        self.invocations.append(action.action)
        if replayed:
            prior = self.results[key]
            return prior.model_copy(update={"replayed": True})
        lineage = []
        candidates = []
        if action.action in {"list_knowledge_documents", "find_knowledge_documents"}:
            observation = KnowledgeCatalogPageV1(
                result_type="knowledge_catalog_page",
                documents=[
                    KnowledgeDocumentDescriptorV1(
                        document_handle=h, display_name=h[-1] + ".pdf",
                        media_type="application/pdf", modalities=["text"], tags=[], version_label="v1",
                    ) for h in sorted(self.docs)
                ], next_cursor=None,
            )
            candidates = sorted(self.docs)
            pages, searches = 1, 0
        elif action.action == "discover_relevant_documents":
            self.backend_calls.append(action.action)
            observation = RelevantDocumentDiscoveryResultV1(
                result_type="relevant_document_discovery_result",
                candidates=[
                    RelevantDocumentCandidateV1(
                        document_handle=handle,
                        document_display_name=f"{handle[-1]}.pdf",
                        media_type="application/pdf",
                        modalities=["text"],
                        preview=(
                            "保留政策候選內容"
                            if "保留" in action.query_text
                            else "Retention policy candidate"
                        ),
                        locator_label=f"{handle[-1]}.pdf · p. 1",
                        page_number=1,
                    )
                    for handle in sorted(self.docs)[: action.limit]
                ],
                ranking_contract="equal-reciprocal-rank-v1",
                channels=["lexical", "vector"],
                degraded=False,
                vector_coverage=2,
                catalog_document_count=2,
                truncated_by_budget=False,
            )
            candidates = [
                item.document_handle for item in observation.candidates
            ]
            pages, searches = 0, 0
        elif action.action == "inspect_knowledge":
            self.backend_calls.append(action.action)
            observation = KnowledgeInspectionResultV1(
                result_type="knowledge_inspection_result",
                items=[KnowledgeInspectionItemV1(
                    evidence_handle=h,
                    document_handle=self.evidence[h].document_handle,
                    document_display_name=self.evidence[h].document_handle[-1] + ".pdf",
                    locator_label="p.1", content="content", modalities=["text"]
                ) for h in action.handles],
            )
            pages = searches = 0
        elif action.action == "inspect_visual":
            self.backend_calls.append(action.action)
            parent = self.visual_bboxes.get(
                action.handle, (0, 0, 10_000, 10_000)
            )
            if action.scope == "full":
                bbox = parent
            else:
                width = parent[2] - parent[0]
                height = parent[3] - parent[1]
                bbox = (
                    parent[0] + width * action.bbox.left // 10_000,
                    parent[1] + height * action.bbox.top // 10_000,
                    parent[0] + width * action.bbox.right // 10_000,
                    parent[1] + height * action.bbox.bottom // 10_000,
                )
            visual_handle = f"kh_visual_{len(self.visual_bboxes) + 1}"
            self.visual_bboxes[visual_handle] = bbox
            content = f"image:{bbox}".encode()
            image_digest = hashlib.sha256(content).hexdigest()
            observation = VisualInspectionResultV1(
                result_type="visual_inspection_result",
                visual_handle=visual_handle,
                source_handle=action.handle,
                page_handle="kh_page_A",
                document_handle="kh_document_A",
                page_number=1,
                scope=action.scope,
                bbox={
                    "left": bbox[0], "top": bbox[1],
                    "right": bbox[2], "bottom": bbox[3],
                },
                image_ref=f"image:{image_digest}",
                image_digest=image_digest,
                width=800,
                height=600,
            )
            item = RetrievalEvidenceLineageV1(
                evidence_handle=visual_handle,
                evidence_ref=(
                    "visual|kh_document_A|1|"
                    + ",".join(str(value) for value in bbox)
                    + f"|{image_digest}"
                ),
                evidence_digest=_hash(["visual", bbox, image_digest]),
                evidence_identity=f"VISUAL:{bbox}:{image_digest}",
                document_handle="kh_document_A",
                result_ref=f"result:{key}",
                result_digest=_hash(["result", key]),
                invocation_ordinal=invocation_ordinal,
            )
            self.evidence[visual_handle] = item
            lineage = [item]
            pages = searches = 0
            visual_image = VisualImagePayloadV1(
                visual_handle=visual_handle,
                image_ref=observation.image_ref,
                image_digest=image_digest,
                width=800,
                height=600,
                content=content,
            )
        else:
            self.backend_calls.append(action.action)
            suffix = (
                action.query_text.replace(" ", "_")
                if action.action == "search_knowledge" else action.direction
            )
            document_handle = "kh_document_B" if "B" in suffix else "kh_document_A"
            item_count = (
                20
                if action.action == "search_knowledge"
                and action.query_text.startswith("bulk")
                else 1
            )
            handles = [
                f"kh_evidence_{suffix}_{index}" if item_count > 1 else f"kh_evidence_{suffix}"
                for index in range(item_count)
            ]
            descriptors = [
                EvidenceDescriptorV1(
                    evidence_handle=handle,
                    document_handle=document_handle,
                    document_display_name=document_handle[-1] + ".pdf",
                    locator_label=f"p.{index + 1}",
                    snippet="evidence",
                    modalities=["text"],
                    page_handle=f"kh_page_{document_handle[-1]}",
                    page_number=index + 1,
                )
                for index, handle in enumerate(handles)
            ]
            observation = (
                KnowledgeSearchResultV1(result_type="knowledge_search_result", evidence=descriptors, next_cursor=None)
                if action.action == "search_knowledge"
                else KnowledgeExpansionResultV1(
                    result_type="knowledge_expansion_result",
                    direction=action.direction,
                    evidence=descriptors,
                )
            )
            lineage = [
                RetrievalEvidenceLineageV1(
                    evidence_handle=handle,
                    evidence_ref=f"evidence-ref:{handle}",
                    evidence_digest=_hash(["evidence", handle]),
                    evidence_identity=(
                        f"INTERNAL:{suffix}"
                        if item_count == 1
                        else f"INTERNAL:{handle}"
                    ),
                    document_handle=document_handle,
                    result_ref=f"result:{key}",
                    result_digest=_hash(["result", key]),
                    invocation_ordinal=invocation_ordinal,
                )
                for handle in handles
            ]
            self.evidence.update(
                {item.evidence_handle: item for item in lineage}
            )
            candidates = [document_handle]
            pages, searches = 0, int(action.action == "search_knowledge")
        tool_tokens = (
            len(
                tiktoken.get_encoding(tokenizer_profile).encode(
                    json.dumps(
                        observation.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                )
            )
            if action.action == "discover_relevant_documents"
            else 256
        )
        envelope = RetrievalInvocationEnvelopeV1(
            observation=observation, result_ref=f"result:{key}", result_digest=_hash(["result", key]),
            document_candidate_handles=candidates, evidence_lineage=lineage,
            catalog_pages=pages, search_rounds=searches, tool_tokens=tool_tokens, replayed=False,
            visual_image=(visual_image if action.action == "inspect_visual" else None),
        )
        self.results[key] = envelope
        return envelope

    def materialize_evidence_pack(self, *, execution_id, catalog_ref, evidence_handles, idempotency_key):
        self.materialized = True
        self.pack = EvidencePackRefV1(
            evidence_pack_ref="evidence-pack-1", execution_id=execution_id, catalog_ref=catalog_ref,
            items=[EvidencePackLineageItemV1(
                evidence_handle=(item := self.evidence[h]).evidence_handle,
                evidence_ref=item.evidence_ref, evidence_digest=item.evidence_digest,
                resource_ref=f"resource:{item.document_handle}", lifecycle_epoch=1,
                document_version_ref=f"version:{item.document_handle}",
                processing_revision_ref=f"revision:{item.document_handle}",
                processing_generation_ref=f"processing:{item.document_handle}",
                index_generation_ref=f"index:{item.document_handle}",
                result_ref=item.result_ref, invocation_ordinal=item.invocation_ordinal,
            ) for h in evidence_handles], digest=DIGEST, created_at=NOW,
        )
        return self.pack

    def read_governance_evidence_pack(
        self, *, execution_id, catalog_ref, evidence_pack_ref, evidence_pack_digest
    ):
        self.governance_reads += 1
        assert self.pack is not None
        assert (
            execution_id,
            catalog_ref,
            evidence_pack_ref,
            evidence_pack_digest,
        ) == (
            self.pack.execution_id,
            self.pack.catalog_ref,
            self.pack.evidence_pack_ref,
            self.pack.digest,
        )
        return GovernanceEvidencePackV1(
            evidence_pack_ref=evidence_pack_ref,
            evidence_pack_digest=evidence_pack_digest,
            execution_id=execution_id,
            catalog_ref=catalog_ref,
            items=[
                GovernanceEvidenceItemV1(
                    evidence_handle=item.evidence_handle,
                    evidence_ref=item.evidence_ref,
                    evidence_digest=item.evidence_digest,
                    result_ref=item.result_ref,
                    invocation_ordinal=item.invocation_ordinal,
                    locator_label="p.1",
                    snippet="evidence",
                    content="exact evidence content",
                    modalities=["text"],
                )
                for item in self.pack.items
            ],
        )

    def read_declared_evidence_subset(
        self, *, execution_id, catalog_ref, handles, visual_images
    ):
        self.governance_reads += 1
        first_positions = {}
        items = []
        mappings = []
        subset_positions = {}
        images_by_handle = {
            image.visual_handle: image for image in visual_images
        }
        for position, handle in enumerate(handles, start=1):
            if handle in first_positions:
                continue
            first_positions[handle] = position
            lineage = self.evidence.get(handle)
            if lineage is None:
                continue
            subset_position = len(items) + 1
            subset_positions[handle] = subset_position
            handle_kind = "visual" if handle.startswith("kh_visual") else "evidence"
            items.append(
                DeclaredEvidenceItemV1(
                    subset_position=subset_position,
                    first_declared_position=position,
                    evidence_handle=handle,
                    handle_kind=handle_kind,
                    evidence_ref=lineage.evidence_ref,
                    evidence_digest=lineage.evidence_digest,
                    source_result_ref=lineage.result_ref,
                    source_result_digest=lineage.result_digest,
                    source_invocation_ordinal=lineage.invocation_ordinal,
                    observations=[
                        ModelVisibleEvidenceObservationV1(
                            result_ref=lineage.result_ref,
                            result_digest=lineage.result_digest,
                            invocation_ordinal=lineage.invocation_ordinal,
                            result_type=(
                                "visual_inspection_result"
                                if handle_kind == "visual"
                                else "knowledge_search_result"
                            ),
                            content_kind=(
                                "visual" if handle_kind == "visual" else "snippet"
                            ),
                            locator_label="p.1",
                            model_visible_content="model-visible evidence",
                            modalities=(
                                ["figure"] if handle_kind == "visual" else ["text"]
                            ),
                        )
                    ],
                )
            )
        for position, handle in enumerate(handles, start=1):
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
        return DeclaredEvidenceSubsetV1(
            execution_id=execution_id,
            catalog_ref=catalog_ref,
            mappings=mappings,
            items=items,
            digest=_hash(
                [
                    execution_id,
                    catalog_ref,
                    [item.model_dump(mode="json") for item in mappings],
                    [item.model_dump(mode="json") for item in items],
                ]
            ),
            visual_images=[
                images_by_handle[item.evidence_handle]
                for item in items
                if item.handle_kind == "visual"
            ],
        )


class Evaluator:
    def __init__(self, results=None, outcomes=()):
        self.calls = 0
        self.results = (
            [PostHocAnswerAssessmentV2(id="s1", status="success")]
            if results is None
            else list(results)
        )
        self.outcomes = list(outcomes)

    def assess(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        answer = kwargs["finalized_answer"]
        subset = kwargs["declared_evidence_subset"]
        consistency = (
            outcome
            if isinstance(outcome, str)
            else "insufficient"
            if any(result.status == "failure" for result in self.results)
            else "aligned"
        )
        results = (
            [PostHocAnswerAssessmentV2(id="s1", status="failure")]
            if isinstance(outcome, str)
            and consistency in {"conflict", "insufficient"}
            else self.results
        )
        return PostHocAnswerAssessmentResultV2(
            state="completed",
            consistency=consistency,
            reason_code=(
                f"declared_evidence_{consistency}"
                if consistency != "aligned"
                else "aligned"
            ),
            answer_digest=hashlib.sha256(
                json.dumps(
                    answer.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=str,
                ).encode()
            ).hexdigest(),
            declared_subset_digest=subset.digest,
            visual_image_digests=[
                image.image_digest for image in subset.visual_images
            ],
            results=results,
            assessment_input_digest="1" * 64,
            assessment_output_digest="2" * 64,
        )


class Governance:
    def __init__(self, order): self.order = order; self.command = None
    def materialize_v2(self, command):
        self.order.append("governance"); self.command = command
        return GovernedAnswerDraftV2(
            draft_ref=command.draft_ref, execution_id=command.execution_id,
            retrieval_status=command.retrieval_status,
            evidence_review_status="questionable",
            evidence_review_reason_codes=["assessment_not_completed"],
            declared_evidence_mappings=command.declared_evidence_mappings,
            assessment_state=command.assessment_state,
            assessment_reason_code=command.assessment_reason_code,
            assessment_version=command.assessment_version,
            assessment_consistency=command.assessment_consistency,
            assessment_answer_digest=command.assessment_answer_digest,
            assessment_declared_subset_digest=(
                command.assessment_declared_subset_digest
            ),
            assessment_visual_image_digests=(
                command.assessment_visual_image_digests
            ),
            assessment_input_digest=command.assessment_input_digest,
            assessment_output_digest=command.assessment_output_digest,
            assessment_results=command.assessment_results,
            segments=[GovernedAnswerSegmentV2(
                segment_id=s.segment_id, text=s.text
            ) for s in command.finalized_answer.segments], digest=DIGEST, created_at=NOW,
        )


class Citation:
    def __init__(self, order): self.order = order
    def materialize_v2(self, command):
        self.order.append("citation")
        return CitationBindingDraftV2(
            draft_ref=command.draft_ref, execution_id=command.execution_id,
            governed_answer_draft_ref=command.governed_answer.draft_ref,
            governed_answer_digest=command.governed_answer.digest,
            bindings=[], digest=DIGEST, created_at=NOW,
        )


class Audit:
    def __init__(self, order): self.order = order; self.command = None
    def materialize_v2(self, command):
        self.order.append("audit"); self.command = command
        return TurnAuditDraftV2(**command.model_dump(exclude={"idempotency_key"}), digest=DIGEST, created_at=NOW)


def search(query, docs=("kh_document_A",)):
    return {
        "action": "search_knowledge", "query_text": query, "document_handles": list(docs),
        "required_modalities": [], "facet_hints": {"document_types": [], "date_from": None,
        "date_to": None, "languages": [], "tags": []}, "limit": 1, "max_output_tokens": 256,
    }


def finalize(claimed_evidence_handles=()):
    return FinalizeAnswerV1(action="finalize_answer", segments=[{
        "segment_id": "s1", "text": "answer"
    }], claimed_evidence_handles=list(claimed_evidence_handles))


def process_evaluation(cycle, verdict):
    return ReasoningEvaluationV1(
        cycle=cycle,
        verdict=verdict,
        finding_codes=[] if verdict == "accept" else ["coverage_gap"],
        summary="Process review completed.",
        score=ProcessScoreV1(
            plan_coverage=2,
            evidence_handling=1,
            conflict_handling=1,
            gap_resolution=1,
            revision_completion=1,
            total=6,
        ),
    )


def _orchestrator(
    actions,
    *,
    policy=None,
    model_fail=False,
    results=None,
    reasoning_mode="standard",
    reasoning_evaluations=(),
    planner_failures=0,
    replanner_failures=0,
    assessment_outcomes=(),
):
    runtime = Runtime(policy=policy, reasoning_mode=reasoning_mode)
    retrieval = Retrieval()
    order = []
    model = ScriptModel(actions, fail=model_fail)
    reasoning_model = ScriptReasoningModel(
        evaluations=reasoning_evaluations,
        planner_failures=planner_failures,
        replanner_failures=replanner_failures,
    )
    evaluator = Evaluator(results, assessment_outcomes)
    orchestrator = StatelessTurnExecutionOrchestrator(
        runtime=runtime, model=model, model_inputs=Inputs(), retrieval=retrieval,
        result_governance=Governance(order), citation=Citation(order), audit=Audit(order),
        evaluator=evaluator,
        reasoning_model=reasoning_model,
    )
    orchestrator.test_evaluator = evaluator
    orchestrator.test_reasoning_model = reasoning_model
    return orchestrator, runtime, retrieval, model, order


def test_repeated_interleaved_multicall_and_terminal_materialization_order():
    actions = [
        search("A"), search("B", ["kh_document_B"]),
        {"action": "inspect_knowledge", "handles": ["kh_evidence_B"], "max_output_tokens": 256},
        {
            "action": "expand_knowledge", "anchor_handles": ["kh_evidence_B"],
            "direction": "next_page", "limit": 1, "max_output_tokens": 256,
        },
        search("C"), finalize(["kh_evidence_B", "kh_evidence_B", "unknown-handle"]),
    ]
    orchestrator, runtime, retrieval, model, order = _orchestrator(actions)
    orchestrator.run("exec-1")
    assert retrieval.invocations == [
        "search_knowledge", "search_knowledge", "inspect_knowledge",
        "expand_knowledge", "search_knowledge",
    ]
    assert runtime.snapshot_value.state == ExecutionState.TERMINAL_COMPLETED
    assert order == ["governance", "citation", "audit"]
    assert orchestrator._audit.command.claimed_evidence_handles == [
        "kh_evidence_B",
        "kh_evidence_B",
        "unknown-handle",
    ]
    assert retrieval.materialized is True
    assert retrieval.governance_reads == 1
    assert orchestrator.test_evaluator.calls == 1
    governed_command = orchestrator._result_governance.command
    assert [item.model_dump() for item in governed_command.evidence_lineage] == [
        {
            "evidence_handle": "kh_evidence_B",
            "evidence_ref": retrieval.evidence["kh_evidence_B"].evidence_ref,
            "evidence_digest": retrieval.evidence["kh_evidence_B"].evidence_digest,
            "result_ref": retrieval.evidence["kh_evidence_B"].result_ref,
            "invocation_ordinal": retrieval.evidence[
                "kh_evidence_B"
            ].invocation_ordinal,
        }
    ]
    assert runtime.calls.index("prepare_terminal") > len(
        [c for c in runtime.calls if c.startswith("complete:")]
    )
    assert runtime.calls[-1] == "commit_terminal"
    assert model.session.discarded is True


def test_standard_mode_does_not_call_planner_or_process_evaluator() -> None:
    orchestrator, runtime, _retrieval, _model, _order = _orchestrator([finalize()])

    orchestrator.run("exec-1")

    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_COMPLETED
    assert orchestrator.test_reasoning_model.plan_calls == []
    assert orchestrator.test_reasoning_model.evaluation_calls == []
    assert runtime.reasoning_events == []


def test_deep_mode_plans_evaluates_and_persists_completed_trace() -> None:
    orchestrator, runtime, _retrieval, model, _order = _orchestrator(
        [finalize()],
        reasoning_mode="deep",
        reasoning_evaluations=[process_evaluation(1, "accept")],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_COMPLETED
    assert trace is not None and trace.status == "completed"
    assert trace.termination_reason == "completed"
    assert len(trace.plans[0].items) == 1
    assert len(trace.evaluations) == 1
    assert trace.evaluations[0].score.total == 6
    assert trace.corrections == []
    assert model.session.reasoning_feedback == []
    assert runtime.snapshot_value.budget.provider_invocations == 3


def test_deep_conflict_revises_rechecks_and_reuses_only_latest_assessment() -> None:
    orchestrator, runtime, _retrieval, _model, _order = _orchestrator(
        [
            search("A"),
            finalize(["kh_evidence_A"]),
            FinalizeAnswerV1(
                action="finalize_answer",
                segments=[{"segment_id": "s1", "text": "revised answer"}],
                claimed_evidence_handles=["kh_evidence_A"],
            ),
        ],
        reasoning_mode="deep",
        reasoning_evaluations=[
            process_evaluation(1, "revise_only"),
            process_evaluation(2, "accept"),
        ],
        assessment_outcomes=["conflict", "aligned"],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_COMPLETED
    assert trace is not None
    assert [check.consistency for check in trace.provisional_evidence_checks] == [
        "conflict",
        "aligned",
    ]
    assert [
        check.candidate_disposition for check in trace.provisional_evidence_checks
    ] == ["revised", "accepted"]
    assert trace.evaluations[0].finding_codes[-1] == "declared_evidence_conflict"
    assert orchestrator.test_evaluator.calls == 2
    assert orchestrator._result_governance.command.assessment_consistency == "aligned"


def test_deep_partial_unresolved_is_insufficient_until_revised() -> None:
    orchestrator, runtime, _retrieval, _model, _order = _orchestrator(
        [
            search("A"),
            finalize(["kh_evidence_A", "kh_missing"]),
            finalize(["kh_evidence_A"]),
        ],
        reasoning_mode="deep",
        reasoning_evaluations=[
            process_evaluation(1, "revise_only"),
            process_evaluation(2, "accept"),
        ],
        assessment_outcomes=["aligned", "aligned"],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert trace is not None
    assert trace.provisional_evidence_checks[0].consistency == "insufficient"
    assert trace.provisional_evidence_checks[0].reason_code == (
        "partially_unresolved_declared_evidence"
    )
    assert trace.evaluations[0].finding_codes[-1] == (
        "declared_evidence_insufficient"
    )
    assert trace.provisional_evidence_checks[1].consistency == "aligned"


def test_deep_empty_declaration_skips_gate_provider_but_still_evaluates() -> None:
    orchestrator, runtime, _retrieval, _model, _order = _orchestrator(
        [finalize()],
        reasoning_mode="deep",
        reasoning_evaluations=[process_evaluation(1, "accept")],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert trace is not None
    assert orchestrator.test_evaluator.calls == 0
    assert trace.provisional_evidence_checks[0].consistency == "not_applicable"
    assert orchestrator._result_governance.command.assessment_consistency == (
        "not_applicable"
    )


def test_deep_gate_unavailable_is_not_retried_and_governs_questionable() -> None:
    unavailable = ClaimAssessmentUnavailable(
        "provider unavailable", reason_code="provider_failed"
    )
    orchestrator, runtime, _retrieval, _model, _order = _orchestrator(
        [search("A"), finalize(["kh_evidence_A"])],
        reasoning_mode="deep",
        reasoning_evaluations=[process_evaluation(1, "accept")],
        assessment_outcomes=[unavailable],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert trace is not None and trace.status == "degraded"
    assert trace.termination_reason == "provisional_evidence_unavailable"
    assert orchestrator.test_evaluator.calls == 1
    assert orchestrator._result_governance.command.assessment_state == "unavailable"
    assert orchestrator._result_governance.command.assessment_consistency == (
        "unavailable"
    )


def test_deep_limit_final_candidate_is_gated_without_another_process_evaluation() -> None:
    policy = RoutePolicyV1(max_reasoning_revision_cycles=0)
    orchestrator, runtime, _retrieval, _model, _order = _orchestrator(
        [
            search("A"),
            finalize(["kh_evidence_A"]),
            FinalizeAnswerV1(
                action="finalize_answer",
                segments=[{"segment_id": "s1", "text": "limited final"}],
                claimed_evidence_handles=["kh_evidence_A"],
            ),
        ],
        policy=policy,
        reasoning_mode="deep",
        reasoning_evaluations=[process_evaluation(1, "revise_only")],
        assessment_outcomes=["insufficient", "aligned"],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert trace is not None
    assert len(trace.evaluations) == 1
    assert len(trace.provisional_evidence_checks) == 2
    assert trace.provisional_evidence_checks[1].candidate_kind == "limit_final"
    assert trace.provisional_evidence_checks[1].linked_evaluation_cycle is None
    assert trace.provisional_evidence_checks[1].candidate_disposition == (
        "limit_finalized"
    )
    assert orchestrator.test_evaluator.calls == 2


def test_deep_research_correction_replans_opens_tools_and_evaluates_new_candidate() -> None:
    orchestrator, runtime, retrieval, model, _order = _orchestrator(
        [finalize(), search("missing evidence"), finalize()],
        reasoning_mode="deep",
        reasoning_evaluations=[
            process_evaluation(1, "research_then_revise"),
            process_evaluation(2, "accept"),
        ],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_COMPLETED
    assert trace is not None and trace.termination_reason == "completed"
    assert [plan.generation for plan in trace.plans] == [1, 2]
    assert len(trace.evaluations) == 2
    assert len(trace.corrections) == 1
    correction = trace.corrections[0]
    assert correction.kind == "research_then_revise"
    assert correction.plan_generation == 2
    assert (correction.tool_invocation_start, correction.tool_invocation_end) == (1, 1)
    assert correction.result_evaluation == 2
    assert retrieval.invocations == ["search_knowledge"]
    assert model.session.reasoning_feedback[0][1].generation == 2


def test_deep_research_correction_cannot_finalize_without_a_tool_invocation() -> None:
    orchestrator, runtime, retrieval, _model, _order = _orchestrator(
        [finalize(), finalize()],
        reasoning_mode="deep",
        reasoning_evaluations=[process_evaluation(1, "research_then_revise")],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_FAILED
    assert trace is not None and trace.termination_reason == "execution_failed"
    assert [plan.generation for plan in trace.plans] == [1, 2]
    assert [evaluation.cycle for evaluation in trace.evaluations] == [1]
    assert trace.corrections == []
    assert retrieval.invocations == []
    assert runtime.calls[-1] == "fail:contract_violation"


def test_deep_mode_runs_bounded_revisions_then_governs_latest_candidate() -> None:
    policy = RoutePolicyV1(max_reasoning_revision_cycles=2)
    orchestrator, runtime, _retrieval, model, _order = _orchestrator(
        [
            finalize(),
            FinalizeAnswerV1(
                action="finalize_answer",
                segments=[{"segment_id": "s1", "text": "revised once"}],
                claimed_evidence_handles=[],
            ),
            FinalizeAnswerV1(
                action="finalize_answer",
                segments=[{"segment_id": "s1", "text": "limited final"}],
                claimed_evidence_handles=[],
            ),
            FinalizeAnswerV1(
                action="finalize_answer",
                segments=[{"segment_id": "s1", "text": "revised twice"}],
                claimed_evidence_handles=[],
            ),
        ],
        policy=policy,
        reasoning_mode="deep",
        reasoning_evaluations=[
            process_evaluation(1, "revise_only"),
            process_evaluation(2, "revise_only"),
            process_evaluation(3, "revise_only"),
        ],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_COMPLETED
    assert trace is not None and trace.termination_reason == "correction_limit_reached"
    assert trace.status == "degraded"
    assert len(trace.evaluations) == 3
    assert len(trace.corrections) == 2
    assert trace.limit_finalization is not None
    assert len(model.session.reasoning_feedback) == 3
    assert orchestrator._result_governance.command.finalized_answer.segments[0].text == (
        "revised twice"
    )


def test_deep_revision_is_completed_only_after_revised_candidate_arrives() -> None:
    orchestrator, runtime, _retrieval, model, _order = _orchestrator(
        [finalize(), RuntimeError("provider unavailable during revision")],
        reasoning_mode="deep",
        reasoning_evaluations=[process_evaluation(1, "revise_only")],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_FAILED
    assert trace is not None and trace.termination_reason == "execution_failed"
    assert trace.corrections == []
    revising_events = [
        event for event in runtime.reasoning_events if event.phase == "revising"
    ]
    assert [(event.progress_status, event.cycle) for event in revising_events] == [
        ("started", 1)
    ]
    assert len(model.session.reasoning_feedback) == 1


def test_deep_evaluator_unavailable_delivers_unscored_degraded_answer() -> None:
    orchestrator, runtime, _retrieval, _model, _order = _orchestrator(
        [finalize()],
        reasoning_mode="deep",
        reasoning_evaluations=[RuntimeError("evaluator unavailable")],
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_COMPLETED
    assert trace is not None and trace.status == "degraded"
    assert trace.termination_reason == "evaluator_unavailable"
    assert trace.evaluations[0].verdict == "unavailable"
    assert trace.evaluations[0].score is None


def test_deep_planner_failure_after_one_schema_repair_fails_closed() -> None:
    orchestrator, runtime, _retrieval, _model, _order = _orchestrator(
        [finalize()],
        reasoning_mode="deep",
        planner_failures=2,
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_FAILED
    assert trace is not None and trace.status == "failed"
    assert trace.termination_reason == "planner_failed"
    assert orchestrator.test_reasoning_model.plan_calls == [False, True]


def test_deep_planner_and_replanner_share_one_schema_repair() -> None:
    orchestrator, runtime, _retrieval, _model, _order = _orchestrator(
        [finalize()],
        reasoning_mode="deep",
        reasoning_evaluations=[process_evaluation(1, "research_then_revise")],
        planner_failures=1,
        replanner_failures=1,
    )

    orchestrator.run("exec-1")

    trace = runtime.snapshot_value.reasoning_trace
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_FAILED
    assert trace is not None and trace.status == "failed"
    assert trace.termination_reason == "replanner_failed"
    assert orchestrator.test_reasoning_model.plan_calls == [False, True]
    assert orchestrator.test_reasoning_model.replan_calls == [False]
    assert [plan.generation for plan in trace.plans] == [1]
    assert trace.corrections == []


def test_two_searches_can_materialize_forty_unique_evidence_items() -> None:
    bulk_search = lambda query: {
        **search(query),
        "limit": 20,
        "max_output_tokens": 64_000,
    }
    orchestrator, runtime, retrieval, _model, order = _orchestrator(
        [bulk_search("bulkA"), bulk_search("bulkB"), finalize()]
    )

    orchestrator.run("exec-1")

    assert runtime.snapshot_value.state == ExecutionState.TERMINAL_COMPLETED
    assert runtime.snapshot_value.budget.unique_evidence == 40
    assert retrieval.pack is not None and len(retrieval.pack.items) == 40
    assert orchestrator._result_governance.command.evidence_lineage == []
    assert orchestrator._audit.command.steps[-3].evidence_count == 0
    assert orchestrator._result_governance.command.assessment_reason_code == (
        "empty_declaration"
    )
    assert order == ["governance", "citation", "audit"]


def test_retry_executions_keep_independent_model_claim_declarations() -> None:
    first, *_ = _orchestrator(
        [search("A"), finalize(["kh_evidence_A", "kh_evidence_A"])]
    )
    second, *_ = _orchestrator(
        [search("A"), finalize(["kh_visual_retry_only"])]
    )

    first.run("exec-1")
    second.run("exec-1")

    assert first._audit.command.claimed_evidence_handles == [
        "kh_evidence_A",
        "kh_evidence_A",
    ]
    assert second._audit.command.claimed_evidence_handles == [
        "kh_visual_retry_only"
    ]


def test_normal_turn_repeated_visual_journey_reaches_governance() -> None:
    actions = [
        search("A"),
        {
            "action": "inspect_visual",
            "handle": "kh_page_A",
            "scope": "full",
            "bbox": None,
        },
        {
            "action": "inspect_visual",
            "handle": "kh_visual_1",
            "scope": "rect",
            "bbox": {
                "left": 2500, "top": 2500,
                "right": 7500, "bottom": 7500,
            },
        },
        finalize(),
    ]
    orchestrator, runtime, retrieval, model, order = _orchestrator(actions)

    orchestrator.run("exec-1")

    assert retrieval.invocations == [
        "search_knowledge", "inspect_visual", "inspect_visual"
    ]
    assert retrieval.visual_bboxes == {
        "kh_visual_1": (0, 0, 10_000, 10_000),
        "kh_visual_2": (2500, 2500, 7500, 7500),
    }
    assert [image.visual_handle for image in model.session.visual_images] == [
        "kh_visual_1", "kh_visual_2"
    ]
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_COMPLETED
    assert runtime.last_reservation.reserve_unique_evidence == 1
    assert order == ["governance", "citation", "audit"]
    assert orchestrator.test_evaluator.calls == 0
    assert [
        item.evidence_handle
        for item in orchestrator._result_governance.command.evidence_lineage
    ] == []


def test_public_model_input_does_not_repeat_session_tool_observation() -> None:
    runtime = Runtime()
    context = SimpleNamespace(
        context_pack_ref="context-1",
        execution_id="exec-1",
        model_user_input="What does the diagram show?",
        recent_tail=[],
        summary=None,
    )
    contexts = SimpleNamespace(get=lambda _ref: context)
    grant_resources = SimpleNamespace(
        grant_document_resources=lambda **_kwargs: SimpleNamespace(resources=[])
    )
    source = PublicOwnerTurnModelInputSource(
        contexts=contexts,
        grant_resources=grant_resources,
        answer_behavior=NullAnswerBehavior(),
    )
    observation = KnowledgeSearchResultV1(
        result_type="knowledge_search_result",
        evidence=[
            EvidenceDescriptorV1(
                evidence_handle="kh_evidence_A",
                document_handle="kh_document_A",
                document_display_name="Layout Guide.pdf",
                locator_label="p. 24",
                snippet="Spark gap figure.",
                modalities=["text"],
                page_handle="kh_page_A",
                page_number=24,
            )
        ],
        next_cursor=None,
    )

    model_input = source.build(
        runtime.snapshot_value,
        observations=[observation],
        contract_repair_remaining=1,
    )

    assert model_input.previous_observation is None
    assert [item.evidence_handle for item in model_input.capabilities.evidence] == [
        "kh_evidence_A"
    ]


def test_public_model_input_discloses_discovery_handles_without_preview_history() -> None:
    runtime = Runtime()
    context = SimpleNamespace(
        context_pack_ref="context-1",
        execution_id="exec-1",
        model_user_input="Which policy is relevant?",
        recent_tail=[],
        summary=None,
    )
    source = PublicOwnerTurnModelInputSource(
        contexts=SimpleNamespace(get=lambda _ref: context),
        grant_resources=SimpleNamespace(
            grant_document_resources=lambda **_kwargs: SimpleNamespace(
                resources=[]
            )
        ),
        answer_behavior=NullAnswerBehavior(),
    )
    discovery = RelevantDocumentDiscoveryResultV1(
        result_type="relevant_document_discovery_result",
        candidates=[
            RelevantDocumentCandidateV1(
                document_handle="kh_document_A",
                document_display_name="Policy A.pdf",
                media_type="application/pdf",
                modalities=["text"],
                preview="selection-only preview",
                locator_label="p. 1",
                page_number=1,
            )
        ],
        ranking_contract="equal-reciprocal-rank-v1",
        channels=["lexical", "vector"],
        degraded=False,
        vector_coverage=2,
        catalog_document_count=2,
        truncated_by_budget=False,
    )

    model_input = source.build(
        runtime.snapshot_value,
        observations=[discovery],
        contract_repair_remaining=1,
    )

    assert model_input.previous_observation is None
    assert [item.document_handle for item in model_input.capabilities.documents] == [
        "kh_document_A"
    ]
    assert "selection-only preview" not in model_input.model_dump_json()


def test_identical_search_replays_and_internal_identity_never_enters_observation():
    orchestrator, runtime, retrieval, model, _ = _orchestrator([search("A"), search("A"), finalize()])
    orchestrator.run("exec-1")
    assert len(retrieval.backend_calls) == 1
    assert any(step.status == "replayed" for step in orchestrator._audit.command.steps)
    assert "INTERNAL:" not in json.dumps(model.session.observations)
    assert runtime.last_unique_identities == ["INTERNAL:A"]
    assert runtime.last_reservation.reserve_document_candidates == 0
    assert runtime.last_reservation.reserve_unique_evidence == 0


@pytest.mark.parametrize(
    "results",
    [
        [
            PostHocAnswerAssessmentV2(
                id="unknown",
                status="success",
            )
        ],
        [
            PostHocAnswerAssessmentV2(
                id="s1",
                status="success",
            ),
            PostHocAnswerAssessmentV2(
                id="s1",
                status="failure",
            )
        ],
    ],
)
def test_invalid_evaluator_semantics_preserve_answer_as_questionable(results):
    orchestrator, runtime, _retrieval, _model, _order = _orchestrator(
        [search("A"), finalize(["kh_evidence_A"])], results=results
    )

    orchestrator.run("exec-1")

    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_COMPLETED
    command = orchestrator._result_governance.command
    assert command.assessment_state == "unavailable"
    assert command.assessment_reason_code == "invalid_output"
    assert command.assessment_results == []
    assert command.finalized_answer.segments[0].text == "answer"
    assessment_step = next(
        step for step in orchestrator._audit.command.steps
        if step.operation == "assess_declared_evidence"
    )
    assert assessment_step.status == "failed"


def test_same_anchor_can_expand_multiple_directions():
    actions = [search("A"),
        {
            "action": "expand_knowledge", "anchor_handles": ["kh_evidence_A"],
            "direction": "previous_page", "limit": 1, "max_output_tokens": 256,
        },
        {
            "action": "expand_knowledge", "anchor_handles": ["kh_evidence_A"],
            "direction": "figure_context", "limit": 1, "max_output_tokens": 256,
        },
        finalize(),
    ]
    orchestrator, runtime, retrieval, _model, _order = _orchestrator(actions)
    orchestrator.run("exec-1")
    assert retrieval.backend_calls == ["search_knowledge", "expand_knowledge", "expand_knowledge"]
    assert runtime.snapshot_value.state == ExecutionState.TERMINAL_COMPLETED


def test_multi_document_discovery_reselection_repeated_search_and_no_tool_finalize():
    list_action = {"action": "list_knowledge_documents", "cursor": None, "page_size": 2, "max_output_tokens": 256}
    orchestrator, runtime, retrieval, _, _ = _orchestrator(
        [
            list_action,
            search("first query", ["kh_document_A"]),
            search("second query", ["kh_document_B"]),
            finalize(),
        ],
    )
    orchestrator.run("exec-1")
    assert retrieval.invocations == [
        "list_knowledge_documents",
        "search_knowledge",
        "search_knowledge",
    ]
    assert retrieval.backend_calls == ["search_knowledge", "search_knowledge"]
    no_tool, rt2, ret2, _, _ = _orchestrator([finalize()])
    no_tool.run("exec-1")
    assert ret2.invocations == [] and rt2.snapshot_value.state == ExecutionState.TERMINAL_COMPLETED
    assert no_tool.test_evaluator.calls == 0


def test_find_uses_runtime_owned_ten_candidate_and_output_reservation():
    orchestrator, runtime, retrieval, _, _ = _orchestrator(
        [
            {
                "action": "find_knowledge_documents",
                "keyword": "RTL8111G",
                "cursor": None,
            },
            finalize(),
        ]
    )
    orchestrator.run("exec-1")

    assert retrieval.invocations == ["find_knowledge_documents"]
    assert runtime.last_reservation.reserve_document_candidates == 10
    assert runtime.last_reservation.reserve_tool_tokens == 64_000
    assert runtime.snapshot_value.budget.document_candidates == 2


def test_discovery_reserves_exact_budget_then_discloses_handles_for_search() -> None:
    orchestrator, runtime, retrieval, model, _ = _orchestrator(
        [
            {
                "action": "discover_relevant_documents",
                "query_text": "保留政策",
                "limit": 2,
            },
            search("retention", ["kh_document_A"]),
            finalize(["kh_evidence_retention"]),
        ]
    )

    orchestrator.run("exec-1")

    assert retrieval.invocations == [
        "discover_relevant_documents",
        "search_knowledge",
    ]
    reservation = runtime.reservations[0]
    assert reservation.reserve_catalog_pages == 1
    assert reservation.reserve_document_candidates == 2
    assert reservation.reserve_search_rounds == 0
    assert reservation.reserve_unique_evidence == 0
    assert reservation.reserve_tool_tokens == 64_000
    assert runtime.snapshot_value.budget.catalog_pages == 1
    assert runtime.snapshot_value.budget.document_candidates == 2
    assert model.session.observations[0]["result_type"] == (
        "relevant_document_discovery_result"
    )
    assert orchestrator._result_governance.command.evidence_lineage[0].evidence_handle == (
        "kh_evidence_retention"
    )
    assert "保留政策候選內容" not in json.dumps(
        orchestrator._result_governance.command.model_dump(mode="json"),
        ensure_ascii=False,
    )


def test_chinese_and_english_discovery_tokens_accumulate_from_actual_results() -> None:
    actions = [
        {
            "action": "discover_relevant_documents",
            "query_text": "保留政策",
            "limit": 1,
        },
        {
            "action": "discover_relevant_documents",
            "query_text": "retention policy",
            "limit": 1,
        },
        finalize(),
    ]
    orchestrator, runtime, retrieval, _, _ = _orchestrator(actions)

    orchestrator.run("exec-1")

    expected = sum(
        envelope.tool_tokens for envelope in retrieval.results.values()
    )
    assert expected > 0
    assert runtime.snapshot_value.budget.tool_tokens == expected
    assert runtime.snapshot_value.budget.catalog_pages == 2


def test_finalize_only_budget_gate_and_fabricated_handle_fails_before_backend():
    policy = RoutePolicyV1(
        max_tool_invocations=0,
        max_provider_invocations=6,
        max_reasoning_revision_cycles=0,
    )
    orchestrator, runtime, _, model, _ = _orchestrator([finalize()], policy=policy)
    orchestrator.run("exec-1")
    assert model.session.finalize_only_values == [True]
    bad, bad_runtime, retrieval, _, _ = _orchestrator([search("bad", ["kh_document_FAKE"])])
    bad.run("exec-1")
    assert retrieval.backend_calls == []
    assert bad_runtime.snapshot_value.state == ExecutionState.TERMINAL_FAILED


def test_navigation_remains_legal_after_catalog_and_search_budgets_are_exhausted():
    runtime = Runtime()
    policy = runtime.snapshot_value.policy
    exhausted = runtime.snapshot_value.model_copy(
        update={
            "budget": _budget(
                catalog_pages=policy.max_catalog_pages,
                search_rounds=policy.max_search_rounds,
                unique_evidence=policy.max_unique_evidence,
            )
        }
    )

    assert _has_legal_tool(
        exhausted, has_documents=True, has_evidence=False
    )


def test_one_contract_violation_repeats_complete_choices_then_allows_legal_repair():
    violation = ModelContractViolationV1(
        safe_code="selection_outside_capabilities",
        action_name="search_knowledge",
    )
    orchestrator, runtime, retrieval, model, _ = _orchestrator(
        [violation, search("A"), finalize()]
    )

    orchestrator.run("exec-1")

    assert retrieval.backend_calls == ["search_knowledge"]
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_COMPLETED
    assert model.session.observations[0]["safe_code"] == "selection_outside_capabilities"
    assert model.session.finalize_only_values == [False, False, False]
    assert runtime.model_action_repairs == [False, True, False]


def test_context_budget_charges_route_tokens_instead_of_utf8_bytes():
    orchestrator, runtime, _, _, _ = _orchestrator([finalize()])

    orchestrator.run("exec-1")

    model_input = Inputs().build(Runtime().snapshot_value)
    byte_count = len(
        json.dumps(
            {"turn_model_input": model_input.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    assert 0 < runtime.snapshot_value.budget.context_tokens < byte_count


def test_live_sized_semantic_capabilities_fit_the_per_invocation_context_budget():
    evidence = [
        EvidenceDescriptorV1(
            evidence_handle=f"kh_evidence_{index}",
            document_handle="kh_document_A",
            document_display_name="RTL8106E Layout Guide V1.0",
            locator_label=f"p. {index + 1}",
            snippet=("晶片、magnetics 與 RJ-45 的配置關係。" * 70),
            modalities=["text"],
            page_handle=None,
            page_number=None,
        )
        for index in range(2)
    ]
    observation = KnowledgeSearchResultV1(
        result_type="knowledge_search_result", evidence=evidence, next_cursor=None
    )
    runtime = Runtime()
    snapshot = runtime.snapshot_value.model_copy(
        update={
            "budget": runtime.snapshot_value.budget.model_copy(
                update={"provider_invocations": 2, "context_tokens": 3000}
            )
        }
    )
    session = ScriptSession([])

    reserved = _context_token_reservation(
        Inputs(),
        snapshot,
        [observation],
        1,
        lambda value, *, finalize_only: session.estimate_next_request_tokens(
            value, finalize_only=finalize_only
        ),
        finalize_only=False,
    )

    assert reserved <= snapshot.policy.context_token_budget


def test_second_contract_violation_fails_closed_without_retrieval_backend_call():
    violation = ModelContractViolationV1(
        safe_code="selection_outside_capabilities",
        action_name="inspect_knowledge",
    )
    orchestrator, runtime, retrieval, model, _ = _orchestrator(
        [violation, violation]
    )

    orchestrator.run("exec-1")

    assert retrieval.backend_calls == []
    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_FAILED
    assert runtime.calls[-1] == "fail:contract_violation"
    assert model.session.finalize_only_values == [False, False]


def test_provider_failure_is_terminal_and_orchestrator_has_safe_diagnostics(caplog):
    orchestrator, runtime, _, _, _ = _orchestrator([], model_fail=True)
    orchestrator.run("exec-1")
    assert runtime.snapshot_value.state == ExecutionState.TERMINAL_FAILED
    assert runtime.calls[-1] == "fail:provider_failed"
    assert "failure_code=provider_failed" in caplog.text
    assert "exception_type=builtins.RuntimeError" in caplog.text
    assert "exception_digest=" in caplog.text
    assert "provider unavailable" not in caplog.text
    assert not hasattr(orchestrator, "resume") and not hasattr(orchestrator, "takeover")


@pytest.mark.parametrize(
    "failure_stage",
    [
        "tool",
        "evidence_pack",
        "governance",
        "citation",
        "audit",
        "prepare_terminal",
        "commit_terminal",
    ],
)
def test_every_execution_stage_failure_is_terminal_without_continuation(failure_stage):
    orchestrator, runtime, retrieval, model, _ = _orchestrator(
        [search("A"), finalize()]
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"{failure_stage} carrier lost")

    if failure_stage == "tool":
        retrieval.invoke = fail
    elif failure_stage == "evidence_pack":
        retrieval.materialize_evidence_pack = fail
    elif failure_stage == "governance":
        orchestrator._result_governance.materialize_v2 = fail
    elif failure_stage == "citation":
        orchestrator._citation.materialize_v2 = fail
    elif failure_stage == "audit":
        orchestrator._audit.materialize_v2 = fail
    elif failure_stage == "prepare_terminal":
        runtime.prepare_terminal = fail
    else:
        runtime.commit_terminal = fail

    orchestrator.run("exec-1")

    assert runtime.snapshot_value.state is ExecutionState.TERMINAL_FAILED
    assert runtime.calls[-1].startswith("fail:")
    assert model.session.discarded is True


class Routing:
    def __init__(self, outcomes): self.outcomes = list(outcomes); self.requests = []
    def open_tested_attempt(self, route_id=None):
        return SimpleNamespace(
            route=SimpleNamespace(
                route_id="r1",
                revision=1,
                runtime_policy=SimpleNamespace(
                    revision=1,
                    tokenizer_profile="cl100k_base",
                    context_window_tokens=128000,
                    max_input_tokens_per_invocation=112000,
                    max_output_tokens_per_invocation=16000,
                    max_tool_result_tokens_per_execution=16000,
                    max_total_tokens_per_conversation=256000,
                ),
            ),
            provider=object(),
        )
    def invoke(self, session, request, response_schema): self.requests.append(request); return self.outcomes.pop(0)


def test_provider_adapter_uses_native_tools_single_call_and_typed_tool_result():
    args = search("A")
    call = ProviderFunctionCall(
        call_id="call-1", name="search_knowledge", arguments=args,
        arguments_json=json.dumps(args),
    )
    outcomes = [
        ProviderToolCall(
            provider_request_id="p1", model_ref="m1", finish_reason="tool_calls",
            usage={}, call=call,
            assistant_message=ProviderAssistantToolCallMessage(tool_calls=[call]),
        ),
        ProviderCompleted(
            provider_request_id="p2", model_ref="m1", finish_reason="stop", usage={},
            output=finalize().model_dump(mode="json"),
            assistant_message=ProviderAssistantMessage(content="{}"),
        ),
    ]
    routing = Routing(outcomes)
    model = StrictProviderTurnModel(routing, record_invocations=False)
    runtime = Runtime()
    inputs = Inputs()
    catalog = KnowledgeCatalogPageV1(
        result_type="knowledge_catalog_page",
        documents=[
            KnowledgeDocumentDescriptorV1(
                document_handle="kh_document_A",
                display_name="A.pdf",
                media_type="application/pdf",
                modalities=["text"],
                tags=[],
                version_label="v1",
            )
        ],
        next_cursor=None,
    )
    model_input = inputs.build(runtime.snapshot_value, observations=[catalog])
    session = model.open_session(model_input)
    first = session.next_action(model_input, finalize_only=False)
    assert first.action.action == "search_knowledge"
    assert routing.requests[0].parallel_tool_calls is False and len(routing.requests[0].tools) == 5
    assert len(routing.requests[0].messages) == 3
    assert isinstance(routing.requests[0].messages[0], ProviderSystemMessage)
    assert isinstance(routing.requests[0].messages[1], ProviderUserMessage)
    assert json.loads(routing.requests[0].messages[1].content) == {
        "available_knowledge": {
            "documents": [
                {
                    "document_handle": "kh_document_A",
                    "display_name": "A.pdf",
                    "media_type": "application/pdf",
                    "modalities": ["text"],
                    "tags": [],
                    "version_label": "v1",
                }
            ]
        }
    }
    assert isinstance(routing.requests[0].messages[2], ProviderUserMessage)
    assert routing.requests[0].messages[2].content == model_input.model_user_input
    session.accept_tool_observation(
        KnowledgeSearchResultV1(
            result_type="knowledge_search_result", evidence=[], next_cursor=None
        )
    )
    final = session.next_action(model_input, finalize_only=True)
    assert final.action.action == "finalize_answer"
    assert routing.requests[1].tools == [] and routing.requests[1].tool_choice == "none"
    assert isinstance(routing.requests[1].messages[0], ProviderSystemMessage)
