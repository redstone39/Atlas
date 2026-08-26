"""Production composition for immutable Agent Research acceptance snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from atlas_production.infrastructure.postgres_turn_knowledge_authorization import (
    ProductionAuthorizedGrantResourceSource,
)
from atlas_production.modules.agent_runtime.public import (
    AcceptedResearchSnapshotV1,
    AcceptedScopeSnapshotV1,
    AgentResearchScopeRefV1,
    StartAgentResearchV1,
)
from atlas_production.modules.answer_behavior.public import AnswerBehaviorOwner
from atlas_production.modules.authorization.public import (
    AuthorizationOwner,
    CreateResearchAccessGrantV1,
    MaterializeGrantDocumentResourcesV1,
)
from atlas_production.modules.model_routing.public import ModelRoutingRuntime
from atlas_production.modules.processing_pipeline.public import (
    CreateGenerationRetentionV1,
    GenerationRetentionOwner,
    GenerationRetentionResourceV1,
)
from atlas_production.modules.prompt_skills.public import PromptSkillCatalog
from atlas_production.modules.retrieval.public import RetrievalOwner
from atlas_production.modules.turn_runtime.public import (
    AcceptExecutionV1,
    ActivateResearchExecutionV1,
    AllocateExecutionV1,
    ExecutionPromptSkillSelectionTraceV1,
    ExecutionSnapshotV1,
    ExecutionState,
    FailCarrierExecutionV1,
    LeasePolicyV1,
    RecordExecutionPromptSkillSelectionV1,
    StageAcceptanceResourceV1,
    TurnRuntimeOwner,
    turn_route_snapshots,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _stable_ref(kind: str, *parts: str) -> str:
    return f"{kind}-{_digest([kind, *parts])}"


@dataclass(frozen=True, slots=True)
class ProductionAgentResearchSnapshotBuilder:
    runtime: TurnRuntimeOwner
    authorization: AuthorizationOwner
    knowledge_source: ProductionAuthorizedGrantResourceSource
    generation_retention: GenerationRetentionOwner
    retrieval: RetrievalOwner
    model_routes: ModelRoutingRuntime
    prompt_skill_catalog: PromptSkillCatalog
    answer_behavior: AnswerBehaviorOwner
    lease_policy: LeasePolicyV1

    def __call__(
        self,
        _session: Session,
        actor_id: str,
        project_ids: tuple[str, ...],
        requested_refs: tuple[AgentResearchScopeRefV1, ...],
        research_id: str,
        execution_id: str,
        payload: StartAgentResearchV1,
    ) -> AcceptedResearchSnapshotV1 | None:
        resources = self.knowledge_source.resources_for_research(
            actor_id=actor_id,
            project_ids=project_ids,
        )
        if not resources:
            return None

        route = self.model_routes.tested_route()
        if route is None:
            raise RuntimeError("tested model route is unavailable")
        route_snapshot, route_policy = turn_route_snapshots(
            route,
            self.model_routes.tested_vision_default_route(),
        )
        prompt_skill_catalogs = [
            self.prompt_skill_catalog.current_catalog(category)
            for category in ("understanding", "planner", "answer")
        ]
        answer_behavior = self.answer_behavior.current()
        snapshot = self.runtime.allocate(
            AllocateExecutionV1(
                execution_id=execution_id,
                research_id=research_id,
                actor_id=actor_id,
                holder_id=_stable_ref("carrier", execution_id),
                route_policy=route_policy,
                route=route_snapshot,
                lease_policy=self.lease_policy,
                idempotency_key=payload.idempotency_key,
                operation="agent_research",
                result_kind="agent_research",
                retry_of_turn_id=None,
                input_digest=hashlib.sha256(payload.question.encode("utf-8")).hexdigest(),
                response_language="zh-TW",
                reasoning_mode="deep",
                prompt_skill_catalogs=prompt_skill_catalogs,
                applied_guidance_revision=answer_behavior.revision,
                applied_guidance_digest=answer_behavior.guidance_digest,
            )
        )
        if snapshot.actor_id != actor_id or snapshot.research_id != research_id:
            raise RuntimeError("research execution replay identity changed")
        if snapshot.state not in {
            ExecutionState.ALLOCATED,
            ExecutionState.ACCEPTED,
            ExecutionState.CONTEXT_READY,
        }:
            raise RuntimeError("research execution is not recoverably accepted")

        try:
            self._stage(snapshot, "authorization", "release_turn_grant")
            grant = self.authorization.create_research_grant(
                CreateResearchAccessGrantV1(
                    execution_id=execution_id,
                    research_id=research_id,
                    actor_id=actor_id,
                    scope_ref=_stable_ref("research-scope", actor_id, *project_ids),
                    scope_digest=self._scope_digest(
                        actor_id, project_ids, requested_refs
                    ),
                    deadline_at=snapshot.deadline_at,
                    idempotency_key=_stable_ref("research-grant", execution_id),
                )
            )
            self.authorization.materialize_grant_document_resources(
                MaterializeGrantDocumentResourcesV1(
                    execution_id=execution_id,
                    grant_ref=grant.grant_ref,
                    authorization_revision=grant.authorization_revision,
                    resources=resources,
                    idempotency_key=_stable_ref("research-grant-resources", execution_id),
                )
            )
            self._stage(
                snapshot,
                "processing_pipeline",
                "release_generation_retention",
            )
            retention = self.generation_retention.create_generation_retention(
                CreateGenerationRetentionV1(
                    execution_id=execution_id,
                    resources=[
                        GenerationRetentionResourceV1(
                            document_version_ref=resource.document_version_ref,
                            processing_generation_ref=resource.processing_generation_ref,
                            index_generation_ref=resource.index_generation_ref,
                            manifest_digest=resource.manifest_digest,
                        )
                        for resource in resources
                    ],
                    idempotency_key=_stable_ref(
                        "research-generation-retention", execution_id
                    ),
                )
            )
            self._stage(snapshot, "retrieval", "release_knowledge_catalog")
            catalog = self.retrieval.create_catalog(
                execution_id=execution_id,
                grant_ref=grant.grant_ref,
                generation_retention_ref=retention.retention_ref,
                idempotency_key=_stable_ref("research-catalog", execution_id),
            )
            if snapshot.state is ExecutionState.ALLOCATED:
                snapshot = self.runtime.accept(
                    AcceptExecutionV1(
                        execution_id=execution_id,
                        expected_version=snapshot.version,
                        fencing_token=snapshot.lease.fencing_token,
                        grant_ref=grant.grant_ref,
                        catalog_ref=catalog.catalog_ref,
                    )
                )
            if snapshot.state is ExecutionState.ACCEPTED:
                if not snapshot.prompt_skill_selections:
                    snapshot = self.runtime.record_prompt_skill_selection(
                        RecordExecutionPromptSkillSelectionV1(
                            execution_id=execution_id,
                            expected_version=snapshot.version,
                            fencing_token=snapshot.lease.fencing_token,
                            selection=ExecutionPromptSkillSelectionTraceV1(
                                category="understanding",
                                node="resolver",
                                status="not_applicable",
                            ),
                        )
                    )
                snapshot = self.runtime.activate_research(
                    ActivateResearchExecutionV1(
                        execution_id=execution_id,
                        expected_version=snapshot.version,
                        fencing_token=snapshot.lease.fencing_token,
                    )
                )
            if snapshot.state is not ExecutionState.CONTEXT_READY:
                raise RuntimeError("research execution did not become context-ready")
            return self._accepted_snapshot(
                actor_id=actor_id,
                project_ids=project_ids,
                requested_refs=requested_refs,
                grant_ref=grant.grant_ref,
                grant_digest=grant.digest,
                catalog_ref=catalog.catalog_ref,
                catalog_digest=catalog.digest,
                execution=snapshot,
            )
        except Exception:
            self._fail_partial(snapshot)
            raise

    def fail_uncommitted(self, execution_id: str) -> None:
        self._fail_partial(self.runtime.snapshot(execution_id))

    def _stage(
        self,
        snapshot: ExecutionSnapshotV1,
        owner: Literal["authorization", "processing_pipeline", "retrieval"],
        release_kind: Literal[
            "release_turn_grant",
            "release_generation_retention",
            "release_knowledge_catalog",
        ],
    ) -> None:
        self.runtime.stage_acceptance_resource(
            StageAcceptanceResourceV1(
                execution_id=snapshot.execution_id,
                expected_version=snapshot.version,
                fencing_token=snapshot.lease.fencing_token,
                resource_owner=owner,
                release_kind=release_kind,
            )
        )

    def _fail_partial(self, snapshot: ExecutionSnapshotV1) -> None:
        current = self.runtime.snapshot(snapshot.execution_id)
        if current.state not in {
            ExecutionState.ALLOCATED,
            ExecutionState.ACCEPTED,
            ExecutionState.CONTEXT_READY,
        }:
            return
        self.runtime.fail_carrier(
            FailCarrierExecutionV1(
                execution_id=current.execution_id,
                expected_version=current.version,
                holder_id=current.lease.holder_id,
                expected_lease_version=current.lease.lease_version,
                fencing_token=current.lease.fencing_token,
                failure_code="contract_violation",
                detected_by="runtime_validator",
            )
        )

    @staticmethod
    def _scope_digest(
        actor_id: str,
        project_ids: tuple[str, ...],
        requested_refs: tuple[AgentResearchScopeRefV1, ...],
    ) -> str:
        return _digest(
            {
                "actor_id": actor_id,
                "project_ids": list(project_ids),
                "requested_refs": [
                    item.model_dump(mode="json") for item in requested_refs
                ],
            }
        )

    def _accepted_snapshot(
        self,
        *,
        actor_id: str,
        project_ids: tuple[str, ...],
        requested_refs: tuple[AgentResearchScopeRefV1, ...],
        grant_ref: str,
        grant_digest: str,
        catalog_ref: str,
        catalog_digest: str,
        execution: ExecutionSnapshotV1,
    ) -> AcceptedResearchSnapshotV1:
        scope_digest = self._scope_digest(actor_id, project_ids, requested_refs)
        policy_digest = _digest(execution.policy.model_dump(mode="json"))
        budget_digest = _digest(execution.budget.model_dump(mode="json"))
        return AcceptedResearchSnapshotV1(
            scope=AcceptedScopeSnapshotV1(
                scope_ref=_stable_ref("research-scope", actor_id, *project_ids),
                scope_digest=scope_digest,
                project_ids=list(project_ids),
                requested_refs=list(requested_refs),
            ),
            grant_ref=grant_ref,
            grant_digest=grant_digest,
            catalog_ref=catalog_ref,
            catalog_digest=catalog_digest,
            policy_ref=f"route-policy-{policy_digest}",
            policy_digest=policy_digest,
            budget_ref=f"turn-budget-{budget_digest}",
            budget_digest=budget_digest,
        )


__all__ = ["ProductionAgentResearchSnapshotBuilder"]
