from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Callable, Literal, Protocol, Sequence

from pydantic import ValidationError

from atlas_production.infrastructure.strict_posthoc_claim_evaluator import (
    ClaimAssessmentUnavailable,
)

from atlas_production.modules.audit.public import (
    MaterializeTurnAuditDraftV2,
    TurnAuditDraftOwnerV2,
    TurnAuditStepV1,
)
from atlas_production.modules.citation_preview.public import (
    CitationBindingDraftOwnerV2,
    MaterializeCitationBindingDraftV2,
)
from atlas_production.modules.result_governance.public import (
    AssessmentReasonCodeV2,
    ExecutionEvidenceLineageV1,
    FinalizedAnswerV1,
    MaterializeGovernedAnswerDraftV2,
    PostHocAnswerEvaluatorV2,
    ResultGovernanceDraftOwnerV2,
    RetrievalStatusV1,
)
from atlas_production.modules.retrieval.public import (
    DiscoverRelevantDocumentsV1,
    ExpandKnowledgeV1,
    FindKnowledgeDocumentsV1,
    InspectKnowledgeV1,
    InspectVisualV1,
    KnowledgeToolActionV1,
    KnowledgeToolObservationV1,
    ListKnowledgeDocumentsV1,
    NavigateDocumentV1,
    RetrievalEvidenceLineageV1,
    RetrievalOwner,
    SearchKnowledgeV1,
    VisualImagePayloadV1,
)
from atlas_production.modules.turn_execution.public import (
    FinalizeAnswerV1,
    ModelContractViolationV1,
    StrictTurnModel,
    TurnExecutionOrchestrator,
    TurnModelInputV3,
)
from atlas_production.modules.turn_runtime.public import (
    BeginResultGovernanceV1,
    BeginToolInvocationV1,
    CommitTerminalV1,
    CompleteToolInvocationV1,
    ExecutionSnapshotV1,
    ExecutionState,
    FailCarrierExecutionV1,
    PrepareTerminalV1,
    RequestModelActionV1,
    TERMINAL_STATES,
    TurnRuntimeOwner,
)


logger = logging.getLogger(__name__)
_DISCOVERY_PAGE_SIZE = 10


class TurnModelInputSource(Protocol):
    def build(
        self,
        snapshot: ExecutionSnapshotV1,
        *,
        observations: Sequence[KnowledgeToolObservationV1],
        contract_repair_remaining: int,
    ) -> TurnModelInputV3: ...


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _ref(kind: str, execution_id: str) -> str:
    return f"{kind}:{hashlib.sha256(f'{kind}:{execution_id}'.encode()).hexdigest()}"


def _action_reservation(
    action: KnowledgeToolActionV1,
    snapshot: ExecutionSnapshotV1,
) -> tuple[int, int, int, int, int]:
    admitted_tool_max = snapshot.policy.tool_token_budget
    if isinstance(action, ListKnowledgeDocumentsV1):
        return (1, action.page_size, 0, 0, action.max_output_tokens)
    if isinstance(action, FindKnowledgeDocumentsV1):
        return (
            1,
            _DISCOVERY_PAGE_SIZE,
            0,
            0,
            admitted_tool_max,
        )
    if isinstance(action, DiscoverRelevantDocumentsV1):
        return (
            1,
            action.limit,
            0,
            0,
            admitted_tool_max,
        )
    if isinstance(action, SearchKnowledgeV1):
        # Search is restricted to already disclosed documents, so it cannot
        # add a new document-candidate identity.
        return (0, 0, 1, action.limit, action.max_output_tokens)
    if isinstance(action, InspectKnowledgeV1):
        # Inspected evidence and its documents were already obtained and counted.
        return (0, 0, 0, 0, action.max_output_tokens)
    if isinstance(action, InspectVisualV1):
        return (0, 0, 0, 1, admitted_tool_max)
    if isinstance(action, NavigateDocumentV1):
        return (
            0,
            0,
            1 if action.mode == "search" else 0,
            0,
            action.max_output_tokens,
        )
    assert isinstance(action, ExpandKnowledgeV1)
    # Expansion may surface another authorized binding for related evidence,
    # so preserve the existing candidate reservation.
    return (0, action.limit, 0, action.limit, action.max_output_tokens)


def _has_legal_tool(
    snapshot: ExecutionSnapshotV1, *, has_documents: bool, has_evidence: bool
) -> bool:
    budget = snapshot.budget
    policy = snapshot.policy
    if (
        budget.tool_invocations >= policy.max_tool_invocations
        or budget.tool_tokens >= policy.tool_token_budget
        or policy.tool_token_budget < 256
    ):
        return False
    can_catalog = (
        budget.catalog_pages < policy.max_catalog_pages
    )
    can_search_or_expand = (
        budget.search_rounds < policy.max_search_rounds
        and budget.unique_evidence < policy.max_unique_evidence
    )
    return (
        can_catalog
        or (can_search_or_expand and (has_documents or has_evidence))
        or has_documents
        or has_evidence
    )


def _validate_model_input(snapshot: ExecutionSnapshotV1, model_input: TurnModelInputV3) -> None:
    if (
        model_input.execution_id != snapshot.execution_id
        or model_input.context_pack_ref != snapshot.context_pack_ref
        or model_input.knowledge_catalog_ref != snapshot.catalog_ref
        or model_input.budget != snapshot.budget
        or model_input.policy != snapshot.policy
        or model_input.capabilities.execution_id != snapshot.execution_id
        or model_input.capabilities.catalog_ref != snapshot.catalog_ref
    ):
        raise ValueError("model input does not match the authoritative runtime snapshot")


def _context_token_reservation(
    source: TurnModelInputSource,
    snapshot: ExecutionSnapshotV1,
    observations: Sequence[KnowledgeToolObservationV1],
    contract_repair_remaining: int,
    count_tokens: Callable[[TurnModelInputV3, bool], int],
    *,
    finalize_only: bool,
) -> int:
    """Converge on the route-tokenized size of the post-CAS model input."""

    estimate = count_tokens(
        source.build(
            snapshot,
            observations=observations,
            contract_repair_remaining=contract_repair_remaining,
        ),
        finalize_only=finalize_only,
    )
    for _ in range(8):
        predicted_budget = snapshot.budget.model_copy(
            update={
                "provider_invocations": snapshot.budget.provider_invocations + 1,
                "context_tokens": snapshot.budget.context_tokens + estimate,
            }
        )
        predicted = snapshot.model_copy(
            update={
                "state": ExecutionState.AWAITING_MODEL_ACTION,
                "version": snapshot.version + 1,
                "budget": predicted_budget,
            }
        )
        required = count_tokens(
            source.build(
                predicted,
                observations=observations,
                contract_repair_remaining=contract_repair_remaining,
            ),
            finalize_only=finalize_only,
        )
        if required <= estimate:
            return estimate
        estimate = required
    return estimate + 32


class StatelessTurnExecutionOrchestrator(TurnExecutionOrchestrator):
    """Sequential owner-call coordinator with no repository or durable state."""

    def __init__(
        self,
        *,
        runtime: TurnRuntimeOwner,
        model: StrictTurnModel,
        model_inputs: TurnModelInputSource,
        retrieval: RetrievalOwner,
        result_governance: ResultGovernanceDraftOwnerV2,
        citation: CitationBindingDraftOwnerV2,
        audit: TurnAuditDraftOwnerV2,
        evaluator: PostHocAnswerEvaluatorV2,
    ) -> None:
        self._runtime = runtime
        self._model = model
        self._model_inputs = model_inputs
        self._retrieval = retrieval
        self._result_governance = result_governance
        self._citation = citation
        self._audit = audit
        self._evaluator = evaluator

    def run(self, execution_id: str) -> None:
        snapshot = self._runtime.snapshot(execution_id)
        if snapshot.state is not ExecutionState.CONTEXT_READY:
            raise ValueError("orchestrator only starts a fresh context_ready execution")
        if snapshot.grant_ref is None or snapshot.catalog_ref is None or snapshot.context_pack_ref is None:
            raise ValueError("accepted execution refs are incomplete")

        observations: list[KnowledgeToolObservationV1] = []
        contract_repair_remaining = 1
        document_candidate_handles: set[str] = set()
        evidence_by_handle: dict[str, RetrievalEvidenceLineageV1] = {}
        visual_images_by_handle: dict[str, VisualImagePayloadV1] = {}
        completed_actions: set[str] = set()
        audit_steps: list[TurnAuditStepV1] = []
        used_retrieval = False
        retrieval_error_status: RetrievalStatusV1 | None = None
        session = None
        contract_repair_request = False
        failure_code = "contract_violation"
        step_ordinal = 0
        try:
            initial_input = self._model_inputs.build(
                snapshot,
                observations=observations,
                contract_repair_remaining=contract_repair_remaining,
            )
            _validate_model_input(snapshot, initial_input)
            session = self._model.open_session(initial_input)

            while True:
                if datetime.now(timezone.utc) >= snapshot.deadline_at:
                    failure_code = "deadline_exceeded"
                    raise TimeoutError("turn deadline elapsed")
                finalize_only = not _has_legal_tool(
                    snapshot,
                    has_documents=bool(document_candidate_handles),
                    has_evidence=bool(evidence_by_handle),
                )
                context_tokens = _context_token_reservation(
                    self._model_inputs,
                    snapshot,
                    observations,
                    contract_repair_remaining,
                    lambda value, *, finalize_only: session.estimate_next_request_tokens(
                        value, finalize_only=finalize_only
                    ),
                    finalize_only=finalize_only,
                )
                failure_code = "budget_exhausted"
                snapshot = self._runtime.request_model_action(
                    RequestModelActionV1(
                        execution_id=execution_id,
                        expected_version=snapshot.version,
                        fencing_token=snapshot.lease.fencing_token,
                        context_tokens=context_tokens,
                        contract_repair=contract_repair_request,
                    )
                )
                contract_repair_request = False
                model_input = self._model_inputs.build(
                    snapshot,
                    observations=observations,
                    contract_repair_remaining=contract_repair_remaining,
                )
                _validate_model_input(snapshot, model_input)
                if (
                    session.estimate_next_request_tokens(
                        model_input, finalize_only=finalize_only
                    )
                    > context_tokens
                ):
                    raise ValueError("post-CAS model input exceeded its context reservation")
                failure_code = "provider_failed"
                model_result = session.next_action(
                    model_input, finalize_only=finalize_only
                )
                step_ordinal += 1
                if isinstance(model_result, ModelContractViolationV1):
                    logger.warning(
                        "turn model capability rejected execution_id=%s safe_code=%s action_name=%s repair_remaining=%s",
                        execution_id,
                        model_result.safe_code,
                        model_result.action_name,
                        contract_repair_remaining,
                    )
                    audit_steps.append(
                        TurnAuditStepV1(
                            ordinal=step_ordinal,
                            step_kind="model",
                            operation=(
                                f"{model_result.action_name or 'provider'}:capability_rejected"
                            )[:100],
                            status="failed",
                            safe_input_digest=_digest(model_input.model_dump(mode="json")),
                            input_tokens=model_result.input_tokens,
                            output_tokens=model_result.output_tokens,
                        )
                    )
                    failure_code = "contract_violation"
                    if contract_repair_remaining == 0:
                        raise ValueError("provider repeated an invalid capability selection")
                    contract_repair_remaining = 0
                    session.accept_contract_repair(model_result)
                    contract_repair_request = True
                    continue

                audit_steps.append(
                    TurnAuditStepV1(
                        ordinal=step_ordinal,
                        step_kind="model",
                        operation=model_result.action.action,
                        status="completed",
                        safe_input_digest=_digest(model_input.model_dump(mode="json")),
                        input_tokens=model_result.input_tokens,
                        output_tokens=model_result.output_tokens,
                    )
                )

                action = model_result.action
                if isinstance(action, FinalizeAnswerV1):
                    failure_code = "terminal_materialization_failed"
                    self._materialize_terminal(
                        snapshot=snapshot,
                        proposal=action,
                        evidence_by_handle=evidence_by_handle,
                        visual_images_by_handle=visual_images_by_handle,
                        audit_steps=audit_steps,
                        used_retrieval=used_retrieval,
                        retrieval_error_status=retrieval_error_status,
                        finalize_only=finalize_only,
                    )
                    return

                used_retrieval = True
                arguments = action.model_dump(mode="json")
                if isinstance(
                    action,
                    (FindKnowledgeDocumentsV1, DiscoverRelevantDocumentsV1),
                ):
                    arguments["runtime_max_output_tokens"] = (
                        snapshot.policy.tool_token_budget
                    )
                    arguments["tokenizer_profile"] = snapshot.route.tokenizer_profile
                action_digest = _digest(arguments)
                pages, candidates, searches, evidence, tokens = _action_reservation(
                    action,
                    snapshot,
                )
                # An exact repeat is guaranteed to replay the immutable
                # retrieval result.  Its candidate/evidence identities are
                # already present in the runtime ledgers, so reserving them
                # again would incorrectly reject a legal replay at the unique
                # identity limit.  Search/page/tool-token budgets still count
                # the repeated model-visible observation.
                if action_digest in completed_actions:
                    candidates = 0
                    evidence = 0
                invocation_ordinal = snapshot.budget.tool_invocations + 1
                invocation_id = _ref(
                    "tool-invocation",
                    f"{execution_id}:{invocation_ordinal}:{action_digest}",
                )
                failure_code = "budget_exhausted"
                snapshot = self._runtime.begin_tool(
                    BeginToolInvocationV1(
                        execution_id=execution_id,
                        expected_version=snapshot.version,
                        fencing_token=snapshot.lease.fencing_token,
                        tool_invocation_id=invocation_id,
                        invocation_ordinal=invocation_ordinal,
                        tool_name=action.action,
                        schema_version=f"{action.action.replace('_', '-')}-v1",
                        arguments_digest=action_digest,
                        reserve_catalog_pages=pages,
                        reserve_document_candidates=candidates,
                        reserve_search_rounds=searches,
                        reserve_unique_evidence=evidence,
                        reserve_tool_tokens=tokens,
                    )
                )
                failure_code = "tool_failed"
                envelope = self._retrieval.invoke(
                    execution_id=execution_id,
                    grant_ref=snapshot.grant_ref,
                    catalog_ref=snapshot.catalog_ref,
                    invocation_ordinal=invocation_ordinal,
                    action=action,
                    max_output_tokens=tokens,
                    tokenizer_profile=snapshot.route.tokenizer_profile,
                )
                document_candidate_handles.update(
                    envelope.document_candidate_handles
                )
                for item in envelope.evidence_lineage:
                    current = evidence_by_handle.get(item.evidence_handle)
                    if current is not None and current != item:
                        raise ValueError("retrieval evidence lineage changed within execution")
                    evidence_by_handle[item.evidence_handle] = item
                failure_code = "budget_exhausted"
                snapshot = self._runtime.complete_tool(
                    CompleteToolInvocationV1(
                        execution_id=execution_id,
                        expected_version=snapshot.version,
                        fencing_token=snapshot.lease.fencing_token,
                        tool_invocation_id=invocation_id,
                        invocation_ordinal=invocation_ordinal,
                        result_ref=envelope.result_ref,
                        result_digest=envelope.result_digest,
                        document_candidate_handles=envelope.document_candidate_handles,
                        unique_evidence_identities=list(
                            dict.fromkeys(
                                item.evidence_identity for item in envelope.evidence_lineage
                            )
                        ),
                        catalog_pages=(
                            1
                            if isinstance(action, DiscoverRelevantDocumentsV1)
                            else envelope.catalog_pages
                        ),
                        search_rounds=envelope.search_rounds,
                        tool_tokens=envelope.tool_tokens,
                    )
                )
                completed_actions.add(action_digest)
                if envelope.observation.result_type == "knowledge_tool_error":
                    retrieval_error_status = {
                        "access_denied": "access_denied",
                        "budget_exhausted": "budget_exhausted",
                        "tool_failed": "tool_failed",
                        "invalid_handle": "tool_failed",
                        "catalog_stale": "tool_failed",
                        "navigation_unavailable": None,
                    }[envelope.observation.error_code]
                step_ordinal += 1
                audit_steps.append(
                    TurnAuditStepV1(
                        ordinal=step_ordinal,
                        step_kind="tool",
                        operation=action.action,
                        status="replayed" if envelope.replayed else "completed",
                        safe_input_digest=_digest(arguments),
                        result_ref=envelope.result_ref,
                        result_digest=envelope.result_digest,
                        output_tokens=envelope.tool_tokens,
                        evidence_count=len(envelope.evidence_lineage),
                    )
                )
                observations.append(envelope.observation)
                if envelope.visual_image is None:
                    session.accept_tool_observation(envelope.observation)
                else:
                    current_image = visual_images_by_handle.get(
                        envelope.visual_image.visual_handle
                    )
                    if (
                        current_image is not None
                        and current_image != envelope.visual_image
                    ):
                        raise ValueError(
                            "model-visible visual carrier changed within execution"
                        )
                    visual_images_by_handle[
                        envelope.visual_image.visual_handle
                    ] = envelope.visual_image
                    session.accept_tool_observation(
                        envelope.observation,
                        visual_image=envelope.visual_image,
                    )
        except Exception as error:
            if getattr(error, "safe_code", None) == "context_limit_exceeded":
                failure_code = "context_limit_exceeded"
            logger.error(
                "turn execution failed safely execution_id=%s failure_code=%s "
                "exception_type=%s exception_digest=%s",
                snapshot.execution_id,
                failure_code,
                f"{type(error).__module__}.{type(error).__qualname__}",
                _digest(
                    {
                        "exception_type": (
                            f"{type(error).__module__}.{type(error).__qualname__}"
                        ),
                        "message": str(error),
                    }
                ),
            )
            self._fail_active(snapshot, failure_code)
        finally:
            if session is not None:
                session.discard()

    def _materialize_terminal(
        self,
        *,
        snapshot: ExecutionSnapshotV1,
        proposal: FinalizeAnswerV1,
        evidence_by_handle: dict[str, RetrievalEvidenceLineageV1],
        visual_images_by_handle: dict[str, VisualImagePayloadV1],
        audit_steps: list[TurnAuditStepV1],
        used_retrieval: bool,
        retrieval_error_status: RetrievalStatusV1 | None,
        finalize_only: bool,
    ) -> None:
        execution_id = snapshot.execution_id
        snapshot = self._runtime.begin_governance(
            BeginResultGovernanceV1(
                execution_id=execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                finalize_action_digest=_digest(proposal.model_dump(mode="json")),
            )
        )
        if snapshot.catalog_ref is None:
            raise ValueError("governing execution lost its catalog ref")
        evidence_pack = self._retrieval.materialize_evidence_pack(
            execution_id=execution_id,
            catalog_ref=snapshot.catalog_ref,
            evidence_handles=list(evidence_by_handle),
            idempotency_key=f"{execution_id}:terminal-evidence",
        )
        if evidence_pack.items:
            retrieval_status: RetrievalStatusV1 = "evidence_found"
        elif retrieval_error_status is not None:
            retrieval_status = retrieval_error_status
        elif finalize_only and used_retrieval:
            retrieval_status = "budget_exhausted"
        elif used_retrieval:
            retrieval_status = "no_evidence"
        else:
            retrieval_status = "not_used"
        finalized_answer = FinalizedAnswerV1(
            segments=[segment.model_dump(mode="json") for segment in proposal.segments]
        )
        declared_subset = self._retrieval.read_declared_evidence_subset(
            execution_id=execution_id,
            catalog_ref=snapshot.catalog_ref,
            handles=proposal.claimed_evidence_handles,
            visual_images=list(visual_images_by_handle.values()),
        )
        declared_lineage = [
            ExecutionEvidenceLineageV1(
                evidence_handle=item.evidence_handle,
                evidence_ref=item.evidence_ref,
                evidence_digest=item.evidence_digest,
                result_ref=item.source_result_ref,
                invocation_ordinal=item.source_invocation_ordinal,
            )
            for item in declared_subset.items
        ]
        assessment_state: Literal["completed", "unavailable", "not_attempted"]
        assessment_reason_code: AssessmentReasonCodeV2
        assessment_input_digest: str | None = None
        assessment_output_digest: str | None = None
        assessment_results = []
        if not proposal.claimed_evidence_handles:
            assessment_state = "not_attempted"
            assessment_reason_code = "empty_declaration"
        elif not declared_subset.items:
            assessment_state = "not_attempted"
            assessment_reason_code = "no_resolved_declared_evidence"
        else:
            assessment_state = "unavailable"
            assessment_reason_code = "provider_failed"
        try:
            if declared_subset.items:
                assessment_result = self._evaluator.assess(
                    execution_id=execution_id,
                    finalized_answer=finalized_answer,
                    declared_evidence_subset=declared_subset,
                    deadline_at=snapshot.deadline_at,
                    route=snapshot.route,
                )
                assessment_state = "completed"
                assessment_reason_code = "completed"
                assessment_input_digest = assessment_result.assessment_input_digest
                assessment_output_digest = assessment_result.assessment_output_digest
                assessment_results = assessment_result.results
        except ClaimAssessmentUnavailable as error:
            assessment_state = "unavailable"
            assessment_reason_code = error.reason_code
        assessment_step = TurnAuditStepV1(
            ordinal=len(audit_steps) + 1,
            step_kind="governance",
            operation="assess_declared_evidence",
            status=(
                "skipped"
                if assessment_state == "not_attempted"
                else ("completed" if assessment_state == "completed" else "failed")
            ),
            safe_input_digest=_digest(
                [declared_subset.digest, finalized_answer.model_dump(mode="json")]
            ),
            evidence_count=len(declared_subset.items),
        )
        try:
            governance_command = MaterializeGovernedAnswerDraftV2(
                draft_ref=_ref("governed-answer-draft", execution_id),
                execution_id=execution_id,
                finalized_answer=finalized_answer,
                retrieval_status=retrieval_status,
                declared_evidence_mappings=declared_subset.mappings,
                evidence_lineage=declared_lineage,
                assessment_state=assessment_state,
                assessment_reason_code=assessment_reason_code,
                assessment_input_digest=assessment_input_digest,
                assessment_output_digest=assessment_output_digest,
                assessment_results=assessment_results,
                idempotency_key=f"{execution_id}:governed-answer",
            )
        except ValidationError:
            # Provider/schema-semantic mistakes never make answer text unavailable.
            assessment_step = assessment_step.model_copy(
                update={"status": "failed"}
            )
            governance_command = MaterializeGovernedAnswerDraftV2(
                draft_ref=_ref("governed-answer-draft", execution_id),
                execution_id=execution_id,
                finalized_answer=finalized_answer,
                retrieval_status=retrieval_status,
                declared_evidence_mappings=declared_subset.mappings,
                evidence_lineage=declared_lineage,
                assessment_state="unavailable",
                assessment_reason_code="invalid_output",
                assessment_input_digest=assessment_input_digest,
                assessment_results=[],
                idempotency_key=f"{execution_id}:governed-answer",
            )
        governed = self._result_governance.materialize_v2(governance_command)
        citation = self._citation.materialize_v2(
            MaterializeCitationBindingDraftV2(
                draft_ref=_ref("citation-binding-draft", execution_id),
                execution_id=execution_id,
                governed_answer=governed,
                idempotency_key=f"{execution_id}:citation-binding",
            )
        )
        audit_steps.extend(
            [
                assessment_step,
                TurnAuditStepV1(
                    ordinal=len(audit_steps) + 2,
                    step_kind="governance",
                    operation="materialize_governed_answer",
                    status="completed",
                    safe_input_digest=_digest([evidence_pack.digest, proposal.model_dump(mode="json")]),
                    result_ref=governed.draft_ref,
                    result_digest=governed.digest,
                    evidence_count=len(declared_lineage),
                ),
                TurnAuditStepV1(
                    ordinal=len(audit_steps) + 3,
                    step_kind="citation",
                    operation="materialize_citation_binding",
                    status="completed",
                    safe_input_digest=_digest(governed.digest),
                    result_ref=citation.draft_ref,
                    result_digest=citation.digest,
                    evidence_count=len(citation.bindings),
                ),
            ]
        )
        audit = self._audit.materialize_v2(
            MaterializeTurnAuditDraftV2(
                draft_ref=_ref("turn-audit-draft", execution_id),
                execution_id=execution_id,
                claimed_evidence_handles=proposal.claimed_evidence_handles,
                evidence_pack_ref=evidence_pack.evidence_pack_ref,
                evidence_pack_digest=evidence_pack.digest,
                governed_answer_draft_ref=governed.draft_ref,
                governed_answer_digest=governed.digest,
                citation_binding_draft_ref=citation.draft_ref,
                citation_binding_digest=citation.digest,
                retrieval_status=governed.retrieval_status,
                evidence_review_status=governed.evidence_review_status,
                terminal_status="terminal_completed",
                steps=audit_steps,
                idempotency_key=f"{execution_id}:turn-audit",
            )
        )
        snapshot = self._runtime.prepare_terminal(
            PrepareTerminalV1(
                execution_id=execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                evidence_pack_ref=evidence_pack.evidence_pack_ref,
                governed_answer_draft_ref=governed.draft_ref,
                citation_binding_draft_ref=citation.draft_ref,
                audit_draft_ref=audit.draft_ref,
            )
        )
        if snapshot.terminal_commit_intent_ref is None:
            raise ValueError("runtime did not bind a terminal commit intent")
        self._runtime.commit_terminal(
            CommitTerminalV1(
                execution_id=execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                terminal_commit_intent_ref=snapshot.terminal_commit_intent_ref,
            )
        )

    def _fail_active(self, snapshot: ExecutionSnapshotV1, failure_code: str) -> None:
        try:
            current = self._runtime.snapshot(snapshot.execution_id)
        except Exception:
            current = snapshot
        if current.state in TERMINAL_STATES:
            return
        self._runtime.fail_carrier(
            FailCarrierExecutionV1(
                execution_id=current.execution_id,
                expected_version=current.version,
                holder_id=current.lease.holder_id,
                expected_lease_version=current.lease.lease_version,
                fencing_token=current.lease.fencing_token,
                failure_code=failure_code,
                detected_by="carrier",
            )
        )


__all__ = [
    "StatelessTurnExecutionOrchestrator",
    "TurnModelInputSource",
]
