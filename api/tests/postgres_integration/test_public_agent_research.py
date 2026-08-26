from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, func, select, update

from atlas_production.infrastructure.persistence.agent_runtime import AtlasAgentResearchRow
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasAccessDecisionRow,
    AtlasAgentTokenRow,
    AtlasPermissionGrantRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.persistence.turn_runtime import (
    AtlasTurnAcceptanceResourceRow,
    AtlasTurnExecutionRow,
    AtlasTurnReleaseIntentRow,
    AtlasTurnTerminalIntentRow,
    AtlasTurnTerminalOutcomeRow,
)
from atlas_production.infrastructure.postgres_agent_adapter import (
    PostgresAgentResearchAuthority,
    PostgresAgentResearchStore,
)
from atlas_production.infrastructure.postgres_owner.turn_runtime import (
    PostgresTurnRuntimeOwner,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.infrastructure.turn_execution_research_terminal import (
    PostgresResearchTerminalPublisher,
    ResearchTerminalPublicationConflict,
)
from atlas_production.modules.agent_runtime.public import (
    AcceptedResearchSnapshotV1,
    AcceptedScopeSnapshotV1,
    AgentResearchService,
    ResearchPacketV1,
    SelectedResearchScopeV1,
    AgentResearchScopeRefV1,
    StartAgentResearchV1,
)
from atlas_production.modules.identity_access.security import agent_token_digest
from atlas_production.modules.prompt_skills.public import PromptSkillCatalogRefV1
from atlas_production.modules.turn_runtime.public import (
    AcceptExecutionV1,
    AllocateExecutionV1,
    BeginResultGovernanceV1,
    BindContextV1,
    CommitTerminalV1,
    ExecutionPromptSkillSelectionTraceV1,
    LeasePolicyV1,
    PrepareTerminalV1,
    RecordExecutionPromptSkillSelectionV1,
    RequestModelActionV1,
    RoutePolicyV1,
    TurnRouteSnapshotV2,
)
from tests.public_synthetic_data import (
    PUBLIC_DIGEST_A,
    PUBLIC_DIGEST_B,
    synthetic_research_packet_payload,
)


PREFIX = "public-synthetic-agent-research-"
RAW_TOKEN = "public-synthetic-agent-token-value"
NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)


def _cleanup(runtime: PostgresRuntime) -> None:
    pattern = f"{PREFIX}%"
    with runtime.session_factory() as session, session.begin():
        for table in (
            AtlasTurnReleaseIntentRow,
            AtlasTurnTerminalOutcomeRow,
            AtlasTurnTerminalIntentRow,
            AtlasTurnAcceptanceResourceRow,
            AtlasTurnExecutionRow,
        ):
            session.execute(delete(table).where(table.execution_id.like(pattern)))
        session.execute(
            delete(AtlasAgentResearchRow).where(
                AtlasAgentResearchRow.actor_id.like(pattern)
                | AtlasAgentResearchRow.research_id.like(pattern)
            )
        )
        session.execute(
            delete(AtlasAccessDecisionRow).where(
                AtlasAccessDecisionRow.actor_id.like(pattern)
            )
        )
        session.execute(
            delete(AtlasPermissionGrantRow).where(
                AtlasPermissionGrantRow.subject_id.like(pattern)
            )
        )
        session.execute(
            delete(AtlasAgentTokenRow).where(AtlasAgentTokenRow.actor_id.like(pattern))
        )
        session.execute(delete(AtlasUserRow).where(AtlasUserRow.actor_id.like(pattern)))
        session.execute(delete(AtlasProjectRow).where(AtlasProjectRow.project_id.like(pattern)))


@pytest.fixture(autouse=True)
def clean_public_agent_research_rows(postgres_runtime: PostgresRuntime):
    _cleanup(postgres_runtime)
    yield
    _cleanup(postgres_runtime)


@dataclass(frozen=True)
class _AuditEvent:
    event_id: str = f"{PREFIX}audit-1"


class _AuditWriter:
    def __init__(self) -> None:
        self.events: list[str] = []
    def append_read_audit(self, event_type: str, **_values):
        self.events.append(event_type)
        return _AuditEvent()


def _accepted_snapshot(project_id: str, scope_ref: str) -> AcceptedResearchSnapshotV1:
    return AcceptedResearchSnapshotV1(
        scope=AcceptedScopeSnapshotV1(
            scope_ref=scope_ref,
            scope_digest=PUBLIC_DIGEST_A,
            project_ids=[project_id],
            requested_refs=[AgentResearchScopeRefV1(kind="project", id=project_id)],
        ),
        grant_ref=f"{PREFIX}grant-snapshot",
        grant_digest=PUBLIC_DIGEST_A,
        catalog_ref=f"{PREFIX}catalog-snapshot",
        catalog_digest=PUBLIC_DIGEST_B,
        policy_ref=f"{PREFIX}policy-snapshot",
        policy_digest=PUBLIC_DIGEST_A,
        budget_ref=f"{PREFIX}budget-snapshot",
        budget_digest=PUBLIC_DIGEST_B,
    )


def _seed_current_agent(runtime: PostgresRuntime, suffix: str = "authority") -> tuple[str, str, str]:
    actor_id = f"{PREFIX}{suffix}-agent"
    project_id = f"{PREFIX}{suffix}-project"
    grant_id = f"{PREFIX}{suffix}-grant"
    token_digest = agent_token_digest(RAW_TOKEN)
    with runtime.session_factory() as session, session.begin():
        session.add(
            AtlasUserRow(
                actor_id=actor_id,
                display_name="Public Synthetic Agent",
                email=None,
                system_role="agent",
                password_digest=None,
                active=True,
                actor_type="service_account",
                created_at=NOW,
            )
        )
        session.add(
            AtlasProjectRow(
                project_id=project_id,
                name="Public Synthetic Research Project",
                policy_profile_id="policy-default",
                status="active",
            )
        )
        session.add(
            AtlasPermissionGrantRow(
                grant_id=grant_id,
                project_id=project_id,
                subject_type="service_account",
                subject_id=actor_id,
                role="viewer",
                effect="allow",
                status="active",
                created_at=NOW,
                revoked_at=None,
            )
        )
        session.add(
            AtlasAgentTokenRow(
                token_id=f"{PREFIX}{suffix}-token",
                actor_id=actor_id,
                token_digest=token_digest,
                token_fingerprint=token_digest[:12],
                status="active",
                created_at=NOW,
                revoked_at=None,
            )
        )
    return actor_id, project_id, grant_id


def test_current_authorization_precedes_allocation_and_exact_replay_survives_revocation(
    postgres_runtime: PostgresRuntime,
) -> None:
    actor_id, project_id, grant_id = _seed_current_agent(postgres_runtime)
    built: list[str] = []

    def snapshot_builder(
        _session,
        accepted_actor_id,
        project_ids,
        _requested_refs,
        research_id,
        _execution_id,
        _payload,
    ):
        assert accepted_actor_id == actor_id
        assert project_ids == (project_id,)
        built.append(research_id)
        return _accepted_snapshot(project_id, research_id)

    fenced: list[str] = []
    authority = PostgresAgentResearchAuthority(
        postgres_runtime.session_factory,
        snapshot_builder=snapshot_builder,
        failure_fencer=fenced.append,
    )
    store = PostgresAgentResearchStore(postgres_runtime.session_factory)
    audit = _AuditWriter()
    service = AgentResearchService(authority, store, audit)
    payload = StartAgentResearchV1(
        question="What does the public synthetic project contain?",
        idempotency_key=f"{PREFIX}idempotency-authority",
        scope=SelectedResearchScopeV1(
            refs=[AgentResearchScopeRefV1(kind="project", id=project_id)]
        ),
    )

    denied = service.start(payload=payload, raw_token="public-synthetic-invalid-token")
    assert denied.status == "denied"
    assert built == []
    with postgres_runtime.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(AtlasAgentResearchRow)
        ) == 0

    accepted = service.start(payload=payload, raw_token=RAW_TOKEN)
    assert accepted.status == "accepted"
    assert accepted.record is not None
    assert built == [accepted.record.research_id]

    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(
            update(AtlasAgentTokenRow)
            .where(AtlasAgentTokenRow.actor_id == actor_id)
            .values(status="revoked", revoked_at=NOW)
        )
        session.execute(
            update(AtlasPermissionGrantRow)
            .where(AtlasPermissionGrantRow.grant_id == grant_id)
            .values(status="revoked", revoked_at=NOW)
        )

    replay = service.start(payload=payload, raw_token=RAW_TOKEN)
    assert replay.status == "replayed"
    assert replay.record == accepted.record
    fresh = service.start(
        payload=payload.model_copy(
            update={"idempotency_key": f"{PREFIX}idempotency-fresh"}
        ),
        raw_token=RAW_TOKEN,
    )
    assert fresh.status == "denied"
    assert fresh.error_code == "agent_token_revoked"
    assert built == [accepted.record.research_id]
    assert fenced == []


def _route() -> TurnRouteSnapshotV2:
    return TurnRouteSnapshotV2(
        route_id=f"{PREFIX}route",
        route_revision=1,
        runtime_policy_revision=1,
        tokenizer_profile="cl100k_base",
        context_window_tokens=128_000,
        max_input_tokens_per_invocation=112_000,
        max_output_tokens_per_invocation=16_000,
        max_tool_result_tokens_per_execution=16_000,
        max_total_tokens_per_conversation=256_000,
    )


def _prepared_terminal(runtime: PostgresRuntime, suffix: str):
    research_id = f"{PREFIX}{suffix}"
    execution_id = f"{PREFIX}{suffix}-execution"
    actor_id = f"{PREFIX}{suffix}-agent"
    packet_payload = synthetic_research_packet_payload()
    packet_payload.update(
        research_id=research_id,
        execution_id=execution_id,
        question_ref=f"{PREFIX}question:{suffix}",
        scope_ref=f"{PREFIX}scope:{suffix}",
        scope_digest=PUBLIC_DIGEST_A,
    )
    packet = ResearchPacketV1.materialize(**packet_payload)
    with runtime.session_factory() as session, session.begin():
        session.add(
            AtlasAgentResearchRow(
                research_id=research_id,
                execution_id=execution_id,
                actor_id=actor_id,
                idempotency_key=f"{PREFIX}idempotency-{suffix}",
                request_digest=PUBLIC_DIGEST_A,
                question_ref=packet.question_ref,
                question="Public synthetic atomic publication?",
                output_mode="evidence_packet",
                accepted_snapshot=_accepted_snapshot(
                    f"{PREFIX}{suffix}-project", packet.scope_ref
                ).model_dump(mode="json"),
                status="accepted",
                packet_payload=None,
                packet_ref=None,
                packet_digest=None,
                accepted_at=NOW,
                completed_at=None,
            )
        )
    owner = PostgresTurnRuntimeOwner(runtime.session_factory)
    current = owner.allocate(
        AllocateExecutionV1(
            execution_id=execution_id,
            turn_id=None,
            conversation_id=None,
            research_id=research_id,
            actor_id=actor_id,
            holder_id=f"{PREFIX}holder-{suffix}",
            route_policy=RoutePolicyV1(
                max_tool_invocations=1,
                max_catalog_pages=1,
                max_search_rounds=1,
                max_model_visible_items_per_turn=1,
                max_retrieval_repairs=1,
                max_selected_anchor_pages_per_round=1,
                max_provider_invocations=10,
                max_reasoning_revision_cycles=0,
                max_schema_retries_per_turn=1,
                context_token_budget=32,
                tool_token_budget=32,
                tool_execution_timeout_seconds=30,
                deadline_seconds=120,
            ),
            route=_route(),
            lease_policy=LeasePolicyV1(ttl_seconds=30),
            idempotency_key=f"{PREFIX}allocate-{suffix}",
            operation="agent_research",
            result_kind="agent_research",
            retry_of_turn_id=None,
            input_digest=PUBLIC_DIGEST_B,
            response_language="zh-TW",
            reasoning_mode="standard",
            prompt_skill_catalogs=[
                PromptSkillCatalogRefV1(
                    category="understanding",
                    catalog_revision=1,
                    catalog_digest=PUBLIC_DIGEST_A,
                ),
                PromptSkillCatalogRefV1(
                    category="answer",
                    catalog_revision=1,
                    catalog_digest=PUBLIC_DIGEST_B,
                ),
            ],
            applied_guidance_revision=0,
            applied_guidance_digest=None,
        )
    )
    current = owner.accept(
        AcceptExecutionV1(
            execution_id=execution_id,
            expected_version=current.version,
            fencing_token=current.lease.fencing_token,
            grant_ref=f"{PREFIX}grant-{suffix}",
            catalog_ref=f"{PREFIX}catalog-{suffix}",
        )
    )
    current = owner.record_prompt_skill_selection(
        RecordExecutionPromptSkillSelectionV1(
            execution_id=execution_id,
            expected_version=current.version,
            fencing_token=current.lease.fencing_token,
            selection=ExecutionPromptSkillSelectionTraceV1(
                category="understanding",
                node="resolver",
                status="not_applicable",
            ),
        )
    )
    current = owner.bind_context(
        BindContextV1(
            execution_id=execution_id,
            expected_version=current.version,
            fencing_token=current.lease.fencing_token,
            context_pack_ref=f"{PREFIX}context-{suffix}",
        )
    )
    current = owner.request_model_action(
        RequestModelActionV1(
            execution_id=execution_id,
            expected_version=current.version,
            fencing_token=current.lease.fencing_token,
            context_tokens=1,
        )
    )
    current = owner.begin_governance(
        BeginResultGovernanceV1(
            execution_id=execution_id,
            expected_version=current.version,
            fencing_token=current.lease.fencing_token,
            finalize_action_digest=PUBLIC_DIGEST_A,
        )
    )
    packet_ref = f"{PREFIX}packet-{suffix}"
    current = owner.prepare_terminal(
        PrepareTerminalV1(
            execution_id=execution_id,
            expected_version=current.version,
            fencing_token=current.lease.fencing_token,
            result_kind="agent_research",
            evidence_pack_ref=f"{PREFIX}evidence-pack-{suffix}",
            research_packet_ref=packet_ref,
            research_packet_digest=packet.packet_digest,
            audit_draft_ref=f"{PREFIX}audit-draft-{suffix}",
        )
    )
    command = CommitTerminalV1(
        execution_id=execution_id,
        expected_version=current.version,
        fencing_token=current.lease.fencing_token,
        terminal_commit_intent_ref=current.terminal_commit_intent_ref,
    )
    return owner, packet_ref, packet, command


def test_packet_and_turn_terminal_publish_atomically_and_replay_exactly(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner, packet_ref, packet, command = _prepared_terminal(postgres_runtime, "atomic")
    publisher = PostgresResearchTerminalPublisher(postgres_runtime.session_factory, owner)

    first = publisher.publish(command=command, packet_ref=packet_ref, packet=packet)
    replay = publisher.publish(command=command, packet_ref=packet_ref, packet=packet)

    assert first.research.status == replay.research.status == "completed"
    assert first.research.packet_digest == replay.research.packet_digest == packet.packet_digest
    assert first.execution.state.value == replay.execution.state.value == "terminal_completed"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda _index: publisher.publish(
                    command=command, packet_ref=packet_ref, packet=packet
                ),
                range(2),
            )
        )
    assert {item.research.packet_digest for item in outcomes} == {packet.packet_digest}


def test_agent_owner_failure_leaves_turn_terminal_unpublished(
    postgres_runtime: PostgresRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, packet_ref, packet, command = _prepared_terminal(
        postgres_runtime, "agent-rollback"
    )
    from atlas_production.infrastructure import turn_execution_research_terminal

    def fail_agent_owner(*_args, **_kwargs):
        raise RuntimeError("public synthetic Agent owner failure")

    monkeypatch.setattr(
        turn_execution_research_terminal,
        "_lock_research_packet_in_session",
        fail_agent_owner,
    )
    publisher = PostgresResearchTerminalPublisher(
        postgres_runtime.session_factory, owner
    )
    with pytest.raises(RuntimeError, match="Agent owner failure"):
        publisher.publish(command=command, packet_ref=packet_ref, packet=packet)

    with postgres_runtime.session_factory() as session:
        research = session.get(AtlasAgentResearchRow, packet.research_id)
        execution = session.get(AtlasTurnExecutionRow, packet.execution_id)
        assert research is not None and research.status == "accepted"
        assert research.packet_payload is None
        assert execution is not None and execution.state == "materializing_terminal"


def test_turn_owner_failure_rolls_back_agent_packet_without_repair(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner, packet_ref, packet, command = _prepared_terminal(postgres_runtime, "rollback")

    class _FailingRuntime:
        def _lock_research_terminal_in_session(self, session, command, *, research_id):
            return owner._lock_research_terminal_in_session(
                session, command, research_id=research_id
            )

        def _commit_research_terminal_in_session(self, *_args, **_kwargs):
            raise RuntimeError("public synthetic Turn owner failure")

    publisher = PostgresResearchTerminalPublisher(
        postgres_runtime.session_factory, _FailingRuntime()  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="Turn owner failure"):
        publisher.publish(command=command, packet_ref=packet_ref, packet=packet)

    with postgres_runtime.session_factory() as session:
        research = session.get(AtlasAgentResearchRow, packet.research_id)
        execution = session.get(AtlasTurnExecutionRow, packet.execution_id)
        assert research is not None and research.status == "accepted"
        assert research.packet_payload is None
        assert execution is not None and execution.state == "materializing_terminal"


def test_one_sided_agent_packet_fails_closed_without_repair(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner, packet_ref, packet, command = _prepared_terminal(postgres_runtime, "one-sided")
    from atlas_production.infrastructure.postgres_agent_adapter import (
        _attach_research_packet_in_session,
    )

    with postgres_runtime.session_factory() as session, session.begin():
        _attach_research_packet_in_session(
            session,
            research_id=packet.research_id,
            execution_id=packet.execution_id,
            packet_ref=packet_ref,
            packet=packet,
        )

    publisher = PostgresResearchTerminalPublisher(postgres_runtime.session_factory, owner)
    with pytest.raises(
        ResearchTerminalPublicationConflict,
        match="Agent packet is completed without its Turn terminal",
    ):
        publisher.publish(command=command, packet_ref=packet_ref, packet=packet)
    with postgres_runtime.session_factory() as session:
        execution = session.get(AtlasTurnExecutionRow, packet.execution_id)
        assert execution is not None and execution.state == "materializing_terminal"
