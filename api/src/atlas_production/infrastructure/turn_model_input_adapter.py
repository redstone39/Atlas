"""Read-only assembly of strict turn model input from public owner refs."""

from __future__ import annotations

import hashlib
import json

from collections.abc import Sequence

from atlas_production.modules.agent_runtime.ports import AgentResearchStore
from atlas_production.modules.answer_behavior.public import (
    AnswerBehaviorOwner,
    project_answer_behavior,
)
from atlas_production.modules.authorization.public import GrantDocumentResourceOwner
from atlas_production.modules.context_engineering.public import ContextEngineeringReader
from atlas_production.modules.retrieval.public import KnowledgeToolObservationV1
from atlas_production.modules.turn_execution.public import (
    ResearchModelInputV1,
    TurnModelHistorySummaryV4,
    TurnModelInputV3,
    TurnModelRecentExchangeV3,
)
from atlas_production.modules.turn_runtime.public import ExecutionSnapshotV1

from .turn_capability_projection import project_turn_model_capabilities


class PublicOwnerTurnModelInputSource:
    """Builds one immutable projection; it owns no state or transaction."""

    def __init__(
        self,
        *,
        contexts: ContextEngineeringReader,
        grant_resources: GrantDocumentResourceOwner,
        answer_behavior: AnswerBehaviorOwner,
    ) -> None:
        self._contexts = contexts
        self._grant_resources = grant_resources
        self._answer_behavior = answer_behavior

    def build(
        self,
        snapshot: ExecutionSnapshotV1,
        *,
        observations: Sequence[KnowledgeToolObservationV1],
        contract_repair_remaining: int,
    ) -> TurnModelInputV3:
        if (
            snapshot.context_pack_ref is None
            or snapshot.catalog_ref is None
            or snapshot.grant_ref is None
        ):
            raise ValueError("turn model input requires accepted immutable refs")
        context = self._contexts.get(snapshot.context_pack_ref)
        if context is None or context.execution_id != snapshot.execution_id:
            raise ValueError("context pack is unavailable or belongs to another execution")
        resources = self._grant_resources.grant_document_resources(
            execution_id=snapshot.execution_id, grant_ref=snapshot.grant_ref
        )
        summary = context.summary
        return TurnModelInputV3(
            execution_id=snapshot.execution_id,
            model_user_input=context.model_user_input,
            recent_tail=[
                TurnModelRecentExchangeV3(
                    logical_turn_id=item.logical_turn_id,
                    representative_turn_id=item.representative_turn_id,
                    user_text=item.user_message.text,
                    assistant_text=(
                        None
                        if item.assistant_message is None
                        else item.assistant_message.text
                    ),
                    assistant_authority=(
                        None
                        if item.assistant_message is None
                        else "pending_verification"
                    ),
                    assistant_usage_scope=(
                        None
                        if item.assistant_message is None
                        else "dialogue_context_only"
                    ),
                )
                for item in context.recent_tail
            ],
            summary=(
                None
                if summary is None
                else TurnModelHistorySummaryV4(
                    summary_ref=summary.summary_ref,
                    historical_user_context=summary.historical_user_context,
                    assistant_pending_verification_context=(
                        summary.assistant_pending_verification_context
                    ),
                    digest=summary.digest,
                )
            ),
            context_pack_ref=context.context_pack_ref,
            knowledge_catalog_ref=snapshot.catalog_ref,
            catalog_document_count=len(resources.resources),
            budget=snapshot.budget,
            policy=snapshot.policy,
            route=snapshot.route,
            answer_behavior=project_answer_behavior(
                self._answer_behavior, snapshot
            ),
            capabilities=project_turn_model_capabilities(
                snapshot,
                catalog_document_count=len(resources.resources),
                observations=observations,
                contract_repair_remaining=contract_repair_remaining,
            ),
            # The bound provider session already receives every observation as
            # the exact tool-result message. Repeating that payload here can
            # consume the context needed for the next model action, especially
            # after a page-sized search result followed by inspect_visual.
            previous_observation=None,
        )


class PublicOwnerResearchModelInputSource:
    """Build a research projection from immutable acceptance-owned references."""

    def __init__(
        self,
        *,
        researches: AgentResearchStore,
        grant_resources: GrantDocumentResourceOwner,
    ) -> None:
        self._researches = researches
        self._grant_resources = grant_resources

    def build(
        self,
        snapshot: ExecutionSnapshotV1,
        *,
        observations: Sequence[KnowledgeToolObservationV1],
        contract_repair_remaining: int,
    ) -> ResearchModelInputV1:
        if (
            snapshot.result_kind != "agent_research"
            or snapshot.research_id is None
            or snapshot.catalog_ref is None
            or snapshot.grant_ref is None
        ):
            raise ValueError("research model input requires accepted research refs")
        record = self._researches.find(snapshot.research_id)
        if (
            record is None
            or record.status != "accepted"
            or record.execution_id != snapshot.execution_id
            or record.snapshot.catalog_ref != snapshot.catalog_ref
            or record.snapshot.grant_ref != snapshot.grant_ref
        ):
            raise ValueError("accepted research snapshot is unavailable or mismatched")
        resources = self._grant_resources.grant_document_resources(
            execution_id=snapshot.execution_id,
            grant_ref=snapshot.grant_ref,
        )
        if _canonical_digest(snapshot.policy.model_dump(mode="json")) != (
            record.snapshot.policy_digest
        ):
            raise ValueError("research route policy no longer matches acceptance")
        return ResearchModelInputV1(
            execution_id=snapshot.execution_id,
            research_id=record.research_id,
            question_ref=record.question_ref,
            model_user_input=record.question,
            scope_ref=record.snapshot.scope.scope_ref,
            scope_digest=record.snapshot.scope.scope_digest,
            knowledge_catalog_ref=snapshot.catalog_ref,
            catalog_document_count=len(resources.resources),
            output_mode=record.output_mode,
            budget=snapshot.budget,
            policy=snapshot.policy,
            route=snapshot.route,
            capabilities=project_turn_model_capabilities(
                snapshot,
                catalog_document_count=len(resources.resources),
                observations=observations,
                contract_repair_remaining=contract_repair_remaining,
            ),
            previous_observation=None,
        )


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["PublicOwnerResearchModelInputSource", "PublicOwnerTurnModelInputSource"]
