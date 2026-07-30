from __future__ import annotations

from atlas_production.shared.public import (
    AdminActionResult,
)
from .api_models import (
    AgentProjectGrantStatus,
    AgentTokenIssueRequest,
    AgentTokenIssueResult,
    AgentTokenStatus,
    AgentUserCreateRequest,
    AgentUserCreateResult,
    AgentUserListResult,
    AgentUserStatus,
    AgentUserUpdateRequest,
)
from .records import (
    AgentTokenRecord,
    UserRecord,
)
from atlas_production.shared.public import (
    utc_now_iso,
)
from .agent_contracts import (
    AgentAccessError,
    AgentActionOutcome,
    AgentAuditCommand,
    AgentCreateOutcome,
    AgentTokenOutcome,
)
from .agent_ports import AgentAccessRepository


class AgentAccessService:
    def __init__(self, repository: AgentAccessRepository) -> None:
        self.repository = repository

    def list_agents(self, actor: UserRecord | None) -> AgentUserListResult:
        self._require_system_admin(actor)
        return AgentUserListResult(
            agents=[
                self._agent_status(agent)
                for agent in sorted(
                    self.repository.list_users(),
                    key=lambda item: item.actor_id,
                )
                if agent.actor_type == "service_account"
            ]
        )

    def create_agent(
        self,
        actor: UserRecord | None,
        payload: AgentUserCreateRequest,
    ) -> AgentCreateOutcome:
        actor = self._require_system_admin(actor)
        if self.repository.get_user(payload.actor_id):
            audit = self.repository.append_audit(
                AgentAuditCommand(
                    event_type="agent_user_rejected",
                    actor_id=actor.actor_id,
                    target_ref=f"agent:{payload.actor_id}",
                    message_code='agent.already_exists',
                    metadata={},
                )
            )
            raise AgentAccessError(
                "admin_action_rejected",
                'agent.already_exists',
                409,
                audit.event_id,
                payload.idempotency_key,
                f"agent:{payload.actor_id}",
            )
        agent = UserRecord(
            actor_id=payload.actor_id,
            display_name=payload.display_name,
            email=None,
            system_role="agent",
            password_digest=None,
            actor_type="service_account",
            created_at=utc_now_iso(),
        )
        self.repository.put_user(agent)
        audit = self.repository.append_audit(
            AgentAuditCommand(
                event_type="agent_user_created",
                actor_id=actor.actor_id,
                target_ref=f"agent:{payload.actor_id}",
                message_code='agent.user_is_ready_for_token_issue',
                metadata={"agent_actor_id": payload.actor_id},
            )
        )
        return AgentCreateOutcome(
            AgentUserCreateResult(
                request_id=payload.idempotency_key,
                status="applied",
                agent=self._agent_status(agent),
                message_code='agent.user_is_ready_for_token_issue',
                audit_event_ref=audit.event_id,
            ),
            201,
        )

    def update_agent(
        self,
        actor: UserRecord | None,
        actor_id: str,
        payload: AgentUserUpdateRequest,
    ) -> AgentActionOutcome:
        actor = self._require_system_admin(actor)
        agent = self.repository.get_user(actor_id)
        if not agent or agent.actor_type != "service_account":
            audit = self.repository.append_audit(
                AgentAuditCommand(
                    event_type="agent_user_update_rejected",
                    actor_id=actor.actor_id,
                    target_ref=f"agent:{actor_id}",
                    message_code='agent.user_was_not_found',
                    metadata={},
                )
            )
            raise AgentAccessError(
                "admin_action_rejected",
                'agent.user_was_not_found',
                404,
                audit.event_id,
                payload.idempotency_key,
            )
        if payload.display_name is not None:
            agent.display_name = payload.display_name
        if payload.active is not None:
            agent.active = payload.active
        message_code = 'agent.user_is_updated'
        if payload.active is False:
            message_code = 'agent.user_has_been_deactivated'
        elif payload.active is True:
            message_code = 'agent.user_has_been_reactivated'
        self.repository.put_user(agent)
        audit = self.repository.append_audit(
            AgentAuditCommand(
                event_type="agent_user_updated",
                actor_id=actor.actor_id,
                target_ref=f"agent:{actor_id}",
                message_code=message_code,
                metadata={"active": agent.active},
            )
        )
        return AgentActionOutcome(
            AdminActionResult(
                request_id=payload.idempotency_key,
                status="applied",
                target_ref=f"agent:{actor_id}",
                message_code=message_code,
                audit_event_ref=audit.event_id,
            ),
            200,
        )

    def issue_token(
        self,
        actor: UserRecord | None,
        actor_id: str,
        payload: AgentTokenIssueRequest,
    ) -> AgentTokenOutcome:
        actor = self._require_system_admin(actor)
        agent = self.repository.get_user(actor_id)
        if not agent or agent.actor_type != "service_account" or not agent.active:
            audit = self.repository.append_audit(
                AgentAuditCommand(
                    event_type="agent_token_issue_rejected",
                    actor_id=actor.actor_id,
                    target_ref=f"agent:{actor_id}",
                    message_code='agent.user_was_not_found_or_is_inactive',
                    metadata={},
                )
            )
            raise AgentAccessError(
                "admin_action_rejected",
                'agent.user_was_not_found_or_is_inactive',
                404,
                audit.event_id,
                payload.idempotency_key,
            )
        raw_token, token = self.repository.issue_token(actor_id)
        audit = self.repository.append_audit(
            AgentAuditCommand(
                event_type="agent_token_issued",
                actor_id=actor.actor_id,
                target_ref=f"agent-token:{token.token_id}",
                message_code='agent.token_has_been_issued_copy_it_now',
                metadata={
                    "agent_actor_id": actor_id,
                    "token_fingerprint": token.token_fingerprint,
                },
            )
        )
        return AgentTokenOutcome(
            AgentTokenIssueResult(
                request_id=payload.idempotency_key,
                status="applied",
                raw_token=raw_token,
                token=self._token_status(token),
                message_code='agent.token_has_been_issued_copy_it_now',
                audit_event_ref=audit.event_id,
            ),
            201,
        )

    def revoke_token(
        self,
        actor: UserRecord | None,
        token_id: str,
    ) -> AgentActionOutcome:
        actor = self._require_system_admin(actor)
        token = self.repository.get_token(token_id)
        if not token:
            audit = self.repository.append_audit(
                AgentAuditCommand(
                    event_type="agent_token_revoke_rejected",
                    actor_id=actor.actor_id,
                    target_ref=f"agent-token:{token_id}",
                    message_code='agent.token_was_not_found',
                    metadata={},
                )
            )
            raise AgentAccessError(
                "admin_action_rejected",
                'agent.token_was_not_found',
                404,
                audit.event_id,
                f"revoke-{token_id}",
            )
        token.status = "revoked"
        token.revoked_at = utc_now_iso()
        self.repository.put_token(token)
        audit = self.repository.append_audit(
            AgentAuditCommand(
                event_type="agent_token_revoked",
                actor_id=actor.actor_id,
                target_ref=f"agent-token:{token_id}",
                message_code='agent.token_has_been_revoked',
                metadata={
                    "agent_actor_id": token.actor_id,
                    "token_fingerprint": token.token_fingerprint,
                },
            )
        )
        return AgentActionOutcome(
            AdminActionResult(
                request_id=f"revoke-{token_id}",
                status="applied",
                target_ref=f"agent-token:{token_id}",
                message_code='agent.token_has_been_revoked',
                audit_event_ref=audit.event_id,
            ),
            200,
        )

    def _agent_status(self, agent: UserRecord) -> AgentUserStatus:
        return AgentUserStatus(
            actor_id=agent.actor_id,
            actor_type="service_account",
            display_name=agent.display_name,
            status="active" if agent.active else "inactive",
            tokens=[
                self._token_status(token)
                for token in sorted(
                    self.repository.list_tokens_for_agent(agent.actor_id),
                    key=lambda item: item.created_at,
                )
            ],
            project_grants=[
                AgentProjectGrantStatus(
                    grant_id=grant.grant_id,
                    project_id=grant.project_id,
                    role=grant.role,
                    effect=grant.effect,
                    status="active",
                )
                for grant in sorted(
                    self.repository.list_project_grants(),
                    key=lambda item: item.grant_id,
                )
                if grant.subject_id == agent.actor_id
            ],
        )

    @staticmethod
    def _token_status(token: AgentTokenRecord) -> AgentTokenStatus:
        return AgentTokenStatus(
            token_id=token.token_id,
            token_fingerprint=token.token_fingerprint,
            status=token.status,
            created_at=token.created_at,
            revoked_at=token.revoked_at,
        )

    def _require_system_admin(self, actor: UserRecord | None) -> UserRecord:
        if not actor:
            raise AgentAccessError(
                "unauthenticated",
                'auth.please_sign_in_before_using_admin_tools',
                401,
            )
        if not self.repository.is_system_admin(actor):
            raise AgentAccessError(
                "access_denied",
                'permission.admin_permission_is_required',
                403,
            )
        return actor
