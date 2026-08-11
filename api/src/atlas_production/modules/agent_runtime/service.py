from __future__ import annotations

from dataclasses import dataclass

from .api_models import AgentQueryRequest
from .contracts import AgentQueryOutcomeV1
from .ports import AgentQueryAuditWriter, AgentQueryAuthority


@dataclass(frozen=True, slots=True)
class AgentRuntimeApplication:
    """Own the fail-closed Agent Query use case and its audit decisions."""

    authority: AgentQueryAuthority
    audit_writer: AgentQueryAuditWriter

    def query(
        self, *, payload: AgentQueryRequest, raw_token: str | None
    ) -> AgentQueryOutcomeV1:
        authorization = self.authority.authorize(
            raw_token=raw_token,
            project_id=payload.project_id,
        )
        if authorization.status == "invalid_token":
            return self._outcome(
                payload=payload,
                event_type="agent_query_denied",
                error_code="invalid_agent_token",
                message_code="agent.token_is_missing_or_invalid",
                status_code=401,
                actor_id=None,
                target_ref=None,
                metadata={"reason": "invalid_agent_token"},
            )
        actor_id = authorization.actor_id
        fingerprint = authorization.token_fingerprint
        if authorization.status == "invalid_agent":
            assert actor_id is not None and fingerprint is not None
            return self._outcome(
                payload=payload,
                event_type="agent_query_denied",
                error_code="invalid_agent_token",
                message_code="agent.token_is_missing_or_invalid",
                audit_message_code="agent.user_is_inactive_or_missing",
                status_code=401,
                actor_id=actor_id,
                target_ref=f"agent:{actor_id}",
                metadata={
                    "reason": "invalid_agent_user",
                    "token_fingerprint": fingerprint,
                },
            )
        if authorization.status == "revoked":
            assert actor_id is not None
            assert authorization.token_id is not None and fingerprint is not None
            return self._outcome(
                payload=payload,
                event_type="agent_query_denied",
                error_code="agent_token_revoked",
                message_code="agent.token_has_been_revoked",
                status_code=403,
                actor_id=actor_id,
                target_ref=f"agent-token:{authorization.token_id}",
                metadata={
                    "reason": "agent_token_revoked",
                    "token_fingerprint": fingerprint,
                },
            )
        assert actor_id is not None
        assert authorization.access_decision_id is not None and fingerprint is not None
        if authorization.status == "denied":
            return self._outcome(
                payload=payload,
                event_type="agent_query_denied",
                error_code="agent_project_access_denied",
                message_code="agent.does_not_have_active_access_to_this_project",
                status_code=403,
                actor_id=actor_id,
                target_ref=f"agent:{actor_id}",
                metadata={
                    "reason": "agent_project_access_denied",
                    "access_decision_id": authorization.access_decision_id,
                    "token_fingerprint": fingerprint,
                },
            )
        return self._outcome(
            payload=payload,
            event_type="agent_query_deferred",
            error_code="feature_deferred",
            message_code="agent.query_execution_is_deferred_until_runtime_is_available",
            status_code=501,
            actor_id=actor_id,
            target_ref=f"agent:{actor_id}",
            metadata={
                "access_decision_id": authorization.access_decision_id,
                "token_fingerprint": fingerprint,
            },
        )

    def _outcome(
        self,
        *,
        payload: AgentQueryRequest,
        event_type: str,
        error_code: str,
        message_code: str,
        status_code: int,
        actor_id: str | None,
        target_ref: str | None,
        metadata: dict[str, object],
        audit_message_code: str | None = None,
    ) -> AgentQueryOutcomeV1:
        audit = self.audit_writer.append_read_audit(
            event_type,
            actor_id=actor_id,
            target_ref=target_ref,
            project_id=payload.project_id,
            message_code=audit_message_code or message_code,
            metadata=metadata,
        )
        return AgentQueryOutcomeV1(
            error_code=error_code,
            message_code=message_code,
            status_code=status_code,
            audit_event_ref=audit.event_id,
        )
