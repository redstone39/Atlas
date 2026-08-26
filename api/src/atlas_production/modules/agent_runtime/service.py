from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .api_models import StartAgentResearchV1
from .contracts import AgentResearchReplayConflict, StartAgentResearchOutcomeV1
from .ports import (
    AgentResearchAuditWriter,
    AgentResearchAuthority,
    AgentResearchStore,
)

@dataclass(frozen=True, slots=True)
class AgentResearchService:
    """Accept one independently authorized, immutable single-round research."""

    authority: AgentResearchAuthority
    store: AgentResearchStore
    audit_writer: AgentResearchAuditWriter

    def start(
        self, *, payload: StartAgentResearchV1, raw_token: str | None
    ) -> StartAgentResearchOutcomeV1:
        request_digest = payload.canonical_payload_digest()
        replay_actor_id = self.authority.identify_replay_actor(raw_token=raw_token)
        if replay_actor_id is not None:
            replay = self.store.find_replay(
                actor_id=replay_actor_id,
                idempotency_key=payload.idempotency_key,
            )
            if replay is not None:
                if replay.request_digest != request_digest:
                    raise AgentResearchReplayConflict(
                        "research replay payload conflicts with the original"
                    )
                audit = self.audit_writer.append_read_audit(
                    "agent_research_replayed",
                    actor_id=replay_actor_id,
                    target_ref=f"agent-research:{replay.research_id}",
                    project_id=None,
                    message_code="agent.research_replayed",
                    metadata={
                        "research_id": replay.research_id,
                        "execution_id": replay.execution_id,
                        "output_mode": replay.output_mode,
                    },
                )
                return StartAgentResearchOutcomeV1(
                    status="replayed",
                    audit_event_ref=audit.event_id,
                    record=replay,
                )
        research_id = f"research-{uuid4().hex}"
        execution_id = f"research-execution-{uuid4().hex}"
        acceptance = self.authority.accept_research(
            raw_token=raw_token,
            payload=payload,
            research_id=research_id,
            execution_id=execution_id,
            request_digest=request_digest,
        )
        authorization = acceptance.authorization
        if authorization.status != "allowed":
            actor_id = authorization.actor_id
            reason = {
                "invalid_token": "invalid_agent_token",
                "invalid_agent": "invalid_agent_token",
                "revoked": "agent_token_revoked",
                "denied": "agent_research_scope_denied",
            }[authorization.status]
            message_code = {
                "invalid_token": "agent.token_is_missing_or_invalid",
                "invalid_agent": "agent.token_is_missing_or_invalid",
                "revoked": "agent.token_has_been_revoked",
                "denied": "agent.research_scope_is_not_authorized",
            }[authorization.status]
            audit = self.audit_writer.append_read_audit(
                "agent_research_denied",
                actor_id=actor_id,
                target_ref=None if actor_id is None else f"agent:{actor_id}",
                project_id=None,
                message_code=message_code,
                metadata={"reason": reason},
            )
            return StartAgentResearchOutcomeV1(
                status="denied",
                error_code=reason,
                message_code=message_code,
                audit_event_ref=audit.event_id,
            )

        assert authorization.actor_id is not None
        assert acceptance.record is not None
        record = acceptance.record
        replayed = acceptance.replayed
        audit = self.audit_writer.append_read_audit(
            "agent_research_replayed" if replayed else "agent_research_accepted",
            actor_id=authorization.actor_id,
            target_ref=f"agent-research:{record.research_id}",
            project_id=None,
            message_code=(
                "agent.research_replayed" if replayed else "agent.research_accepted"
            ),
            metadata={
                "research_id": record.research_id,
                "execution_id": record.execution_id,
                "output_mode": record.output_mode,
            },
        )
        return StartAgentResearchOutcomeV1(
            status="replayed" if replayed else "accepted",
            audit_event_ref=audit.event_id,
            record=record,
        )
