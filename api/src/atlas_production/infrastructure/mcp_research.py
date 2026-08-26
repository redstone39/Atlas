from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Annotated, Callable, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from atlas_production.infrastructure.mcp_config import McpTransportConfig
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasAgentTokenRow,
    AtlasPermissionGrantRow,
    AtlasTeamMembershipRow,
    AtlasTeamRow,
    AtlasUserRow,
)
from atlas_production.infrastructure.persistence.project_governance import AtlasProjectRow
from atlas_production.infrastructure.postgres_agent_adapter import PostgresAgentResearchStore
from atlas_production.infrastructure.postgres_owner.audit import AccessDecisionWriter
from atlas_production.infrastructure.postgres_owner.audit_v1 import PostgresAuditV1Store
from atlas_production.infrastructure.postgres_owner.project import ActionAwareAclAuthority
from atlas_production.infrastructure.postgres_owner.result_governance_v1 import (
    PostgresResultGovernanceV1Store,
)
from atlas_production.infrastructure.postgres_owner.citation_v1 import (
    PostgresCitationV1Store,
)
from atlas_production.infrastructure.postgres_owner.turn_runtime import PostgresTurnRuntimeOwner
from atlas_production.infrastructure.postgres_retrieval_v1_actions import _opaque
from atlas_production.infrastructure.postgres_retrieval_v1_adapter import KnowledgeToolService
from atlas_production.infrastructure.postgres_turn_knowledge_rows import (
    PostgresProductionKnowledgeRowSource,
)
from atlas_production.modules.agent_runtime.public import (
    AgentResearchEvidenceContentV1,
    AgentResearchEvidenceProjectionIncomplete,
    AgentResearchEvidenceUnavailable,
    AgentResearchRecordV1,
    AgentResearchReplayConflict,
    AgentResearchScopeV1,
    AgentResearchService,
    StartAgentResearchV1,
)
from atlas_production.modules.audit.public import TurnAuditDraftV2
from atlas_production.modules.citation_preview.public import (
    CitationBindingDraftV2,
    ProtectedDeclaredEvidencePageV1,
    ProtectedDeclaredEvidenceV1,
    ProtectedDeclaredEvidenceReadOwner,
    ReadProtectedDeclaredEvidenceV1,
)
from atlas_production.modules.result_governance.public import GovernedAnswerDraftV2
from atlas_production.modules.retrieval.public import EvidencePackRefV1
from atlas_production.infrastructure.postgres_owner.retrieval_v1 import (
    RetrievalStoreConflict,
)
from atlas_production.modules.turn_runtime.public import TerminalOutcomeV1
from atlas_production.modules.identity_access.security import agent_token_digest


SessionFactory = Callable
McpIdentity = Annotated[str, Field(min_length=1, max_length=200)]
McpQuestion = Annotated[str, Field(min_length=1, max_length=12_000)]
McpIdempotencyKey = Annotated[str, Field(min_length=16, max_length=128)]
McpCursor = Annotated[str, Field(min_length=1, max_length=500)]
McpPageLimit = Annotated[int, Field(ge=1, le=100)]




class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KnowledgeScopeV1(_StrictModel):
    kind: Literal["project", "team"]
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=500)
    project_count: int = Field(ge=1)


class KnowledgeScopePageV1(_StrictModel):
    scopes: list[KnowledgeScopeV1]
    next_cursor: str | None = None


class ResearchAcceptedV1(_StrictModel):
    research_id: str
    execution_id: str
    status: Literal["accepted"]


class ResearchAnswerProjectionV1(_StrictModel):
    status: Literal["not_requested", "available", "unavailable"]
    packet_ref: str | None = None
    packet_digest: str | None = None
    governed_answer: dict[str, object] | None = None
    citations: dict[str, object] | None = None


class ResearchStatusV1(_StrictModel):
    research_id: str
    execution_id: str
    status: Literal["processing", "failed", "completed"]
    failure_code: str | None = None
    packet: dict[str, object] | None = None
    answer: ResearchAnswerProjectionV1 | None = None


EvidenceContentV1 = AgentResearchEvidenceContentV1


@dataclass(frozen=True, slots=True)
class CurrentAgentIdentity:
    status: Literal["allowed", "invalid_token", "invalid_agent", "revoked"]
    actor_id: str | None = None
    token_fingerprint: str | None = None


class McpBusinessError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PostgresMcpAgentAccess:
    session_factory: SessionFactory

    def transport_actor(self, raw_token: str) -> str | None:
        digest = agent_token_digest(raw_token)
        with self.session_factory() as session:
            actor_ids = session.scalars(
                select(AtlasAgentTokenRow.actor_id)
                .where(AtlasAgentTokenRow.token_digest == digest)
                .order_by(AtlasAgentTokenRow.token_id)
                .limit(2)
            ).all()
            return actor_ids[0] if len(actor_ids) == 1 else None

    def _current_identity_in_session(
        self, session: Session, raw_token: str
    ) -> CurrentAgentIdentity:
        digest = agent_token_digest(raw_token)
        tokens = session.scalars(
            select(AtlasAgentTokenRow)
            .where(AtlasAgentTokenRow.token_digest == digest)
            .order_by(AtlasAgentTokenRow.token_id)
            .limit(2)
        ).all()
        if len(tokens) != 1:
            return CurrentAgentIdentity("invalid_token")
        token = tokens[0]
        actor = session.get(AtlasUserRow, token.actor_id)
        if (
            actor is None
            or actor.actor_type != "service_account"
            or not actor.active
        ):
            return CurrentAgentIdentity(
                "invalid_agent",
                actor_id=token.actor_id,
                token_fingerprint=token.token_fingerprint,
            )
        if token.status != "active":
            return CurrentAgentIdentity(
                "revoked",
                actor_id=token.actor_id,
                token_fingerprint=token.token_fingerprint,
            )
        return CurrentAgentIdentity(
            "allowed",
            actor_id=token.actor_id,
            token_fingerprint=token.token_fingerprint,
        )

    def current_identity(self, raw_token: str) -> CurrentAgentIdentity:
        with self.session_factory() as session:
            return self._current_identity_in_session(session, raw_token)

    def list_scopes(self, raw_token: str) -> tuple[str, list[KnowledgeScopeV1]]:
        with self.session_factory() as session, session.begin():
            identity = self._current_identity_in_session(session, raw_token)
            if identity.status != "allowed" or identity.actor_id is None:
                raise McpBusinessError(identity.status)
            actor_id = identity.actor_id
            projects = session.scalars(
                select(AtlasProjectRow)
                .where(AtlasProjectRow.status == "active")
                .order_by(AtlasProjectRow.project_id)
            ).all()
            allowed_project_ids: set[str] = set()
            for project in projects:
                decision = ActionAwareAclAuthority.resolve_in_session(
                    session,
                    actor_type="service_account",
                    actor_id=actor_id,
                    project_id=project.project_id,
                    action="agent_query",
                    lock_rows=False,
                )
                AccessDecisionWriter(session).append(decision)
                if decision.allowed:
                    allowed_project_ids.add(project.project_id)
            projections = [
                KnowledgeScopeV1(
                    kind="project",
                    id=project.project_id,
                    name=project.name,
                    project_count=1,
                )
                for project in projects
                if project.project_id in allowed_project_ids
            ]
            teams = session.scalars(
                select(AtlasTeamRow)
                .join(
                    AtlasTeamMembershipRow,
                    AtlasTeamMembershipRow.team_id == AtlasTeamRow.team_id,
                )
                .where(
                    AtlasTeamRow.status == "active",
                    AtlasTeamMembershipRow.member_actor_type == "service_account",
                    AtlasTeamMembershipRow.member_actor_id == actor_id,
                    AtlasTeamMembershipRow.status == "active",
                )
                .order_by(AtlasTeamRow.team_id)
            ).all()
            for selected_team in teams:
                hierarchy: set[str] = set()
                team: AtlasTeamRow | None = selected_team
                valid = True
                while team is not None:
                    if team.team_id in hierarchy:
                        valid = False
                        break
                    hierarchy.add(team.team_id)
                    if team.parent_team_id is None:
                        break
                    team = session.scalar(
                        select(AtlasTeamRow).where(
                            AtlasTeamRow.team_id == team.parent_team_id,
                            AtlasTeamRow.status == "active",
                        )
                    )
                    if team is None:
                        valid = False
                if not valid:
                    continue
                granted = set(
                    session.scalars(
                        select(AtlasPermissionGrantRow.project_id).where(
                            AtlasPermissionGrantRow.subject_type == "team",
                            AtlasPermissionGrantRow.subject_id.in_(hierarchy),
                            AtlasPermissionGrantRow.status == "active",
                        )
                    ).all()
                )
                expanded = granted & allowed_project_ids
                if expanded:
                    projections.append(
                        KnowledgeScopeV1(
                            kind="team",
                            id=selected_team.team_id,
                            name=selected_team.name,
                            project_count=len(expanded),
                        )
                    )
        projections.sort(key=lambda item: (item.kind, item.id))
        return actor_id, projections


@dataclass(frozen=True, slots=True)
class AgentResearchEvidenceReader:
    access: PostgresMcpAgentAccess
    researches: PostgresAgentResearchStore
    runtime: PostgresTurnRuntimeOwner
    audits: PostgresAuditV1Store
    results: PostgresResultGovernanceV1Store
    citations: PostgresCitationV1Store
    retrieval: KnowledgeToolService
    knowledge: PostgresProductionKnowledgeRowSource
    protected: ProtectedDeclaredEvidenceReadOwner

    def read(
        self,
        *,
        raw_token: str,
        research_id: str,
        evidence_id: str,
        representation: Literal["text", "visual", "native"],
    ) -> tuple[str, EvidenceContentV1]:
        identity = self.access.current_identity(raw_token)
        if identity.status != "allowed" or identity.actor_id is None:
            raise McpBusinessError(identity.status)
        actor_id = identity.actor_id
        record = self.researches.find(research_id)
        if record is None or record.actor_id != actor_id or record.packet is None:
            raise McpBusinessError("research_not_available")
        return actor_id, self._read_record(
            record=record,
            evidence_id=evidence_id,
            representation=representation,
            visibility_actor_id=actor_id,
        )

    def validate_completed(
        self,
        *,
        record: AgentResearchRecordV1,
        terminal: TerminalOutcomeV1,
    ) -> tuple[
        TurnAuditDraftV2,
        GovernedAnswerDraftV2 | None,
        CitationBindingDraftV2 | None,
    ]:
        _, audit, _ = self._completed_lineage(record, terminal)
        governed, citations = self._validate_answer_lineage(
            record=record,
            terminal=terminal,
            audit=audit,
        )
        return audit, governed, citations

    def _validate_answer_lineage(
        self,
        *,
        record: AgentResearchRecordV1,
        terminal: TerminalOutcomeV1,
        audit: TurnAuditDraftV2,
    ) -> tuple[GovernedAnswerDraftV2 | None, CitationBindingDraftV2 | None]:
        answer_ref = terminal.governed_answer_draft_ref
        citation_ref = terminal.citation_binding_draft_ref
        if (answer_ref is None) != (citation_ref is None):
            raise AgentResearchEvidenceProjectionIncomplete(
                "completed research answer lineage is incomplete"
            )
        if record.output_mode == "evidence_packet" and answer_ref is not None:
            raise AgentResearchEvidenceProjectionIncomplete(
                "packet-only research cannot expose answer lineage"
            )
        if answer_ref is None or citation_ref is None:
            return None, None
        governed = self.results.read_v2(answer_ref)
        citations = self.citations.read_v2(citation_ref)
        if (
            governed is None
            or citations is None
            or governed.draft_ref != answer_ref
            or governed.execution_id != record.execution_id
            or governed.research_packet_ref != record.packet_ref
            or governed.research_packet_digest != record.packet_digest
            or citations.draft_ref != citation_ref
            or citations.execution_id != record.execution_id
            or citations.governed_answer_draft_ref != governed.draft_ref
            or citations.governed_answer_digest != governed.digest
            or audit.governed_answer_draft_ref != governed.draft_ref
            or audit.governed_answer_digest != governed.digest
            or audit.citation_binding_draft_ref != citations.draft_ref
            or audit.citation_binding_digest != citations.digest
        ):
            raise AgentResearchEvidenceProjectionIncomplete(
                "completed research answer lineage is inconsistent"
            )
        return governed, citations

    def read_admin(
        self,
        *,
        record: AgentResearchRecordV1,
        evidence_id: str,
        representation: Literal["text", "visual", "native"],
    ) -> AgentResearchEvidenceContentV1:
        try:
            return self._read_record(
                record=record,
                evidence_id=evidence_id,
                representation=representation,
                visibility_actor_id=None,
                fail_closed=True,
            )
        except McpBusinessError as exc:
            raise AgentResearchEvidenceUnavailable(exc.code) from exc

    def _read_record(
        self,
        *,
        record: AgentResearchRecordV1,
        evidence_id: str,
        representation: Literal["text", "visual", "native"],
        visibility_actor_id: str | None,
        fail_closed: bool = False,
    ) -> AgentResearchEvidenceContentV1:
        terminal = self.runtime.terminal_outcome(record.execution_id)
        if terminal is None:
            if fail_closed:
                raise AgentResearchEvidenceProjectionIncomplete(
                    "research terminal is unavailable"
                )
            raise McpBusinessError("research_not_available")
        try:
            _, audit, pack = self._completed_lineage(record, terminal)
            self._validate_answer_lineage(
                record=record,
                terminal=terminal,
                audit=audit,
            )
        except AgentResearchEvidenceProjectionIncomplete as exc:
            if fail_closed:
                raise
            raise McpBusinessError("research_not_available") from exc
        descriptor = next(
            (item for item in record.packet.evidence if item.evidence_id == evidence_id),
            None,
        )
        if descriptor is None or representation not in descriptor.available_representations:
            raise McpBusinessError("evidence_not_available")
        lineage = next(
            (
                item
                for item in pack.items
                if _opaque(
                    "research-evidence",
                    record.execution_id,
                    record.snapshot.catalog_ref,
                    item.evidence_handle,
                    item.evidence_digest,
                )
                == evidence_id
            ),
            None,
        )
        if (
            lineage is None
            or lineage.evidence_handle not in audit.claimed_evidence_handles
        ):
            raise McpBusinessError("evidence_not_available")
        try:
            self._validate_descriptor(
                record=record,
                audit=audit,
                descriptor=descriptor,
            )
        except AgentResearchEvidenceProjectionIncomplete as exc:
            if fail_closed:
                raise
            raise McpBusinessError("research_not_available") from exc
        if visibility_actor_id is not None:
            current_documents = self.knowledge.authorized_documents_for_projects(
                actor_id=visibility_actor_id,
                project_ids=tuple(record.snapshot.scope.project_ids),
            )
            if lineage.document_version_ref not in {
                item.document_version_ref for item in current_documents
            }:
                raise McpBusinessError("evidence_not_available")
        declared = self.audits.read_raw_declared_evidence(record.execution_id)
        if declared is None or lineage.evidence_handle not in declared:
            raise McpBusinessError("evidence_not_available")
        command = ReadProtectedDeclaredEvidenceV1(
            execution_id=record.execution_id,
            declaration_position=declared.index(lineage.evidence_handle) + 1,
            evidence_handle=lineage.evidence_handle,
            evidence_pack_ref=pack.evidence_pack_ref,
            evidence_pack_digest=pack.digest,
            evidence_ref=lineage.evidence_ref,
            evidence_digest=lineage.evidence_digest,
            resource_ref=lineage.resource_ref,
            lifecycle_epoch=lineage.lifecycle_epoch,
            document_version_ref=lineage.document_version_ref,
            processing_revision_ref=lineage.processing_revision_ref,
            processing_generation_ref=lineage.processing_generation_ref,
            index_generation_ref=lineage.index_generation_ref,
            page_artifact_ref=lineage.page_artifact_ref,
            result_ref=lineage.result_ref,
            invocation_ordinal=lineage.invocation_ordinal,
        )
        accepted_media_types = {
            "visual": frozenset({"image/png"}),
            "native": frozenset({"application/pdf"}),
            "text": frozenset(),
        }[representation]
        opened = self.protected.read_protected_declared(
            command,
            accepted_page_media_types=accepted_media_types,
        )
        if representation == "text" and isinstance(
            opened, ProtectedDeclaredEvidenceV1
        ):
            return AgentResearchEvidenceContentV1(
                research_id=record.research_id,
                evidence_id=evidence_id,
                representation=representation,
                media_type="text/plain",
                text=opened.content,
            )
        expected_media = (
            "image/png" if representation == "visual" else "application/pdf"
        )
        if (
            representation != "text"
            and isinstance(opened, ProtectedDeclaredEvidencePageV1)
            and opened.media_type == expected_media
        ):
            return AgentResearchEvidenceContentV1(
                research_id=record.research_id,
                evidence_id=evidence_id,
                representation=representation,
                media_type=expected_media,
                content_base64=base64.b64encode(opened.content).decode("ascii"),
            )
        raise McpBusinessError("evidence_not_available")

    def _validate_descriptor(
        self,
        *,
        record: AgentResearchRecordV1,
        audit: TurnAuditDraftV2,
        descriptor,
    ) -> None:
        try:
            governance_pack = self.retrieval.read_governance_evidence_pack(
                execution_id=record.execution_id,
                catalog_ref=record.snapshot.catalog_ref,
                evidence_pack_ref=audit.evidence_pack_ref,
                evidence_pack_digest=audit.evidence_pack_digest,
            )
            projection = self.retrieval.project_research_evidence(
                execution_id=record.execution_id,
                catalog_ref=record.snapshot.catalog_ref,
                handles=audit.claimed_evidence_handles,
                visual_images=governance_pack.visual_images,
            )
        except (RetrievalStoreConflict, ValueError) as exc:
            raise AgentResearchEvidenceProjectionIncomplete(
                "research evidence projection cannot be reproduced"
            ) from exc
        projected = next(
            (
                item
                for item in projection.items
                if item.evidence_id == descriptor.evidence_id
            ),
            None,
        )
        if (
            projected is None
            or projected.kind != descriptor.kind
            or projected.title != descriptor.title
            or projected.page != descriptor.page
            or projected.locator != descriptor.locator
            or projected.available_representations
            != descriptor.available_representations
            or projected.lineage_digest != descriptor.lineage_digest
        ):
            raise AgentResearchEvidenceProjectionIncomplete(
                "research evidence descriptor lineage is inconsistent"
            )

    def _completed_lineage(
        self,
        record: AgentResearchRecordV1,
        terminal: TerminalOutcomeV1,
    ) -> tuple[TerminalOutcomeV1, TurnAuditDraftV2, EvidencePackRefV1]:
        packet = record.packet
        if (
            record.status != "completed"
            or packet is None
            or record.packet_ref is None
            or record.packet_digest is None
            or packet.research_id != record.research_id
            or packet.execution_id != record.execution_id
            or packet.question_ref != record.question_ref
            or packet.scope_ref != record.snapshot.scope.scope_ref
            or packet.scope_digest != record.snapshot.scope.scope_digest
            or packet.packet_digest != record.packet_digest
            or terminal.execution_id != record.execution_id
            or terminal.outcome != "completed"
            or terminal.result_kind != "agent_research"
            or terminal.research_packet_ref != record.packet_ref
            or terminal.research_packet_digest != record.packet_digest
            or terminal.evidence_pack_ref is None
            or terminal.audit_draft_ref is None
        ):
            raise AgentResearchEvidenceProjectionIncomplete(
                "completed research packet lineage is inconsistent"
            )
        audit = self.audits.read_v2(terminal.audit_draft_ref)
        if (
            audit is None
            or audit.draft_ref != terminal.audit_draft_ref
            or audit.execution_id != record.execution_id
            or audit.research_packet_ref != record.packet_ref
            or audit.research_packet_digest != record.packet_digest
            or audit.evidence_pack_ref != terminal.evidence_pack_ref
            or audit.governed_answer_draft_ref
            != terminal.governed_answer_draft_ref
            or audit.citation_binding_draft_ref
            != terminal.citation_binding_draft_ref
        ):
            raise AgentResearchEvidenceProjectionIncomplete(
                "completed research audit lineage is inconsistent"
            )
        pack = self.retrieval.read_evidence_pack(terminal.evidence_pack_ref)
        if (
            pack is None
            or pack.evidence_pack_ref != terminal.evidence_pack_ref
            or pack.digest != audit.evidence_pack_digest
            or pack.execution_id != record.execution_id
            or pack.catalog_ref != record.snapshot.catalog_ref
        ):
            raise AgentResearchEvidenceProjectionIncomplete(
                "completed research evidence-pack lineage is inconsistent"
            )
        return terminal, audit, pack


@dataclass(frozen=True, slots=True)
class AtlasMcpResearchApplication:
    access: PostgresMcpAgentAccess
    research: AgentResearchService
    researches: PostgresAgentResearchStore
    runtime: PostgresTurnRuntimeOwner
    results: PostgresResultGovernanceV1Store
    citations: object
    evidence: AgentResearchEvidenceReader
    audit_writer: object
    carrier: object

    def _audit(
        self,
        event_type: str,
        *,
        actor_id: str | None,
        target_ref: str | None,
        message_code: str,
        metadata: dict[str, object],
    ) -> None:
        self.audit_writer.append_read_audit(
            event_type,
            actor_id=actor_id,
            target_ref=target_ref,
            project_id=None,
            message_code=message_code,
            metadata=metadata,
        )

    def list_scopes(
        self, *, raw_token: str, cursor: str | None, limit: int
    ) -> KnowledgeScopePageV1:
        actor_id: str | None = None
        try:
            actor_id, scopes = self.access.list_scopes(raw_token)
            offset = _decode_cursor(cursor)
            page = scopes[offset : offset + limit]
            next_cursor = (
                _encode_cursor(offset + len(page))
                if offset + len(page) < len(scopes)
                else None
            )
            self._audit(
                "agent_mcp_list_knowledge_scopes_allowed",
                actor_id=actor_id,
                target_ref=f"agent-mcp-scope:{actor_id}",
                message_code="agent.mcp_scope_listed",
                metadata={
                    "returned_count": len(page),
                    "has_more": next_cursor is not None,
                },
            )
            return KnowledgeScopePageV1(scopes=page, next_cursor=next_cursor)
        except McpBusinessError as exc:
            self._audit(
                "agent_mcp_list_knowledge_scopes_denied",
                actor_id=actor_id,
                target_ref=(
                    None if actor_id is None else f"agent-mcp-scope:{actor_id}"
                ),
                message_code="agent.mcp_scope_denied",
                metadata={"reason": exc.code},
            )
            raise ToolError(exc.code) from exc

    def start(
        self, *, raw_token: str, payload: StartAgentResearchV1
    ) -> ResearchAcceptedV1:
        try:
            outcome = self.research.start(payload=payload, raw_token=raw_token)
        except AgentResearchReplayConflict as exc:
            self._audit(
                "agent_mcp_research_denied",
                actor_id=self.access.transport_actor(raw_token),
                target_ref=None,
                message_code="agent.research_replay_conflict",
                metadata={"reason": "research_replay_conflict"},
            )
            raise ToolError("research_replay_conflict") from exc
        if outcome.record is None:
            raise ToolError(outcome.error_code or "research_denied")
        if outcome.status == "accepted":
            self.carrier.launch(outcome.record.execution_id)
        return ResearchAcceptedV1(
            research_id=outcome.record.research_id,
            execution_id=outcome.record.execution_id,
            status="accepted",
        )

    def get(self, *, raw_token: str, research_id: str) -> ResearchStatusV1:
        identity = self.access.current_identity(raw_token)
        actor_id = identity.actor_id
        try:
            if identity.status != "allowed" or actor_id is None:
                raise McpBusinessError(identity.status)
            record = self.researches.find(research_id)
            if record is None or record.actor_id != actor_id:
                raise McpBusinessError("research_not_available")
            terminal = self.runtime.terminal_outcome(record.execution_id)
            if terminal is None:
                result = ResearchStatusV1(
                    research_id=research_id,
                    execution_id=record.execution_id,
                    status="processing",
                )
            elif terminal.outcome == "failed":
                result = ResearchStatusV1(
                    research_id=research_id,
                    execution_id=record.execution_id,
                    status="failed",
                    failure_code=terminal.failure_code,
                )
            else:
                try:
                    _, governed, citations = self.evidence.validate_completed(
                        record=record,
                        terminal=terminal,
                    )
                except AgentResearchEvidenceProjectionIncomplete as exc:
                    raise McpBusinessError("research_not_available") from exc
                answer = ResearchAnswerProjectionV1(
                    status="not_requested",
                    packet_ref=record.packet_ref,
                    packet_digest=record.packet_digest,
                )
                if record.output_mode == "evidence_packet_and_answer":
                    if governed is None or citations is None:
                        answer = answer.model_copy(update={"status": "unavailable"})
                    else:
                        answer = ResearchAnswerProjectionV1(
                            status="available",
                            packet_ref=record.packet_ref,
                            packet_digest=record.packet_digest,
                            governed_answer=governed.model_dump(mode="json"),
                            citations=citations.model_dump(mode="json"),
                        )
                result = ResearchStatusV1(
                    research_id=research_id,
                    execution_id=record.execution_id,
                    status="completed",
                    packet=record.packet.model_dump(mode="json"),
                    answer=answer,
                )
            self._audit(
                "agent_mcp_get_research_allowed",
                actor_id=actor_id,
                target_ref=f"agent-research:{research_id}",
                message_code="agent.research_read",
                metadata={"status": result.status},
            )
            return result
        except McpBusinessError as exc:
            self._audit(
                "agent_mcp_get_research_denied",
                actor_id=actor_id,
                target_ref=None,
                message_code="agent.research_read_denied",
                metadata={"reason": exc.code},
            )
            raise ToolError(exc.code) from exc

    def read_evidence(
        self,
        *,
        raw_token: str,
        research_id: str,
        evidence_id: str,
        representation: Literal["text", "visual", "native"],
    ) -> EvidenceContentV1:

        try:
            actor_id, result = self.evidence.read(
                raw_token=raw_token,
                research_id=research_id,
                evidence_id=evidence_id,
                representation=representation,
            )
            self._audit(
                "agent_mcp_read_evidence_allowed",
                actor_id=actor_id,
                target_ref=f"agent-research:{research_id}",
                message_code="agent.research_evidence_read",
                metadata={"evidence_id": evidence_id, "representation": representation},
            )
            return result
        except McpBusinessError as exc:
            identity = self.access.current_identity(raw_token)
            self._audit(
                "agent_mcp_read_evidence_denied",
                actor_id=identity.actor_id,
                target_ref=None,
                message_code="agent.research_evidence_denied",
                metadata={"reason": exc.code, "representation": representation},
            )
            raise ToolError(exc.code) from exc


class McpExactPathMiddleware:
    """Route the public exact /mcp endpoint through Starlette's slash mount."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            child_scope = dict(scope)
            child_scope["path"] = "/mcp/"
            child_scope["raw_path"] = b"/mcp/"
            await self._app(child_scope, receive, send)
            return
        await self._app(scope, receive, send)


class McpBusinessAuditMiddleware:
    _EVENTS = {
        "atlas.list_knowledge_scopes": "agent_mcp_list_knowledge_scopes_denied",
        "atlas.research": "agent_mcp_research_denied",
        "atlas.get_research": "agent_mcp_get_research_denied",
        "atlas.read_evidence": "agent_mcp_read_evidence_denied",
    }

    def __init__(
        self,
        *,
        access: PostgresMcpAgentAccess,
        audit_writer: object,
    ) -> None:
        self._access = access
        self._audit_writer = audit_writer

    async def __call__(self, ctx, call_next):
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params or {}
        tool_name = (
            params.get("name")
            if isinstance(params, dict)
            else getattr(params, "name", None)
        )
        try:
            result = await call_next(ctx)
        except Exception:
            self._audit_unrecorded_denial(ctx, tool_name)
            raise
        is_error = (
            result.get("isError", result.get("is_error", False))
            if isinstance(result, dict)
            else getattr(result, "is_error", False)
        )
        if is_error:
            self._audit_unrecorded_denial(ctx, tool_name)
        return result

    def _audit_unrecorded_denial(self, ctx, tool_name: object) -> None:
        request = ctx.request
        if request is None or getattr(
            request.state, "atlas_mcp_business_audited", False
        ):
            return
        event_type = self._EVENTS.get(tool_name)
        if event_type is None:
            return
        actor_id = getattr(request.state, "atlas_agent_actor_id", None)
        self._audit_writer.append_read_audit(
            event_type,
            actor_id=actor_id,
            target_ref=None,
            project_id=None,
            message_code="agent.mcp_tool_denied",
            metadata={"reason": "invalid_tool_call", "tool": tool_name},
        )


def _mark_business_audited(ctx: Context) -> None:
    request = ctx.request_context.request
    if request is not None:
        request.state.atlas_mcp_business_audited = True
class AgentBearerMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        access: PostgresMcpAgentAccess,
        audit_writer: object,
    ) -> None:
        self._app = app
        self._access = access
        self._audit_writer = audit_writer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        header = Headers(scope=scope).get("authorization")
        raw_token = (
            header[7:]
            if header is not None
            and header.startswith("Bearer ")
            and header[7:]
            and header[7:] == header[7:].strip()
            else None
        )
        actor_id = (
            None if raw_token is None else self._access.transport_actor(raw_token)
        )
        if raw_token is None or actor_id is None:
            self._audit_writer.append_read_audit(
                "agent_mcp_transport_auth_denied",
                actor_id=None,
                target_ref=None,
                project_id=None,
                message_code="agent.token_is_missing_or_invalid",
                metadata={"reason": "invalid_agent_token"},
            )
            response = JSONResponse(
                {"error_code": "invalid_agent_token", "message_code": "agent.token_is_missing_or_invalid"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        child_scope = dict(scope)
        state = dict(scope.get("state") or {})
        state["atlas_agent_token"] = raw_token
        state["atlas_agent_actor_id"] = actor_id
        child_scope["state"] = state
        await self._app(child_scope, receive, send)


@dataclass(frozen=True, slots=True)
class AtlasMcpTransport:
    server: MCPServer
    asgi_app: ASGIApp
    config: McpTransportConfig


def _raw_token(ctx: Context) -> str:
    request = ctx.request_context.request
    if request is None:
        raise ToolError("invalid_agent_token")
    raw_token = getattr(request.state, "atlas_agent_token", None)
    if not isinstance(raw_token, str) or not raw_token:
        raise ToolError("invalid_agent_token")
    return raw_token


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"atlas-scope:{offset}".encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        prefix, raw_offset = decoded.split(":", 1)
        offset = int(raw_offset)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise McpBusinessError("invalid_scope_cursor") from exc
    if prefix != "atlas-scope" or offset < 0:
        raise McpBusinessError("invalid_scope_cursor")
    return offset


def build_mcp_transport(
    *,
    application: AtlasMcpResearchApplication,
    access: PostgresMcpAgentAccess,
    audit_writer: object,
    config: McpTransportConfig,
) -> AtlasMcpTransport:
    server = MCPServer(
        name="atlas-research",
        title="Atlas Research",
        description="Single-round evidence-grounded research over authorized Atlas knowledge.",
        version="1.0.0",
        middleware=[
            McpBusinessAuditMiddleware(access=access, audit_writer=audit_writer)
        ],
    )

    @server.tool(name="atlas.list_knowledge_scopes", structured_output=True)
    def list_knowledge_scopes(
        ctx: Context,
        cursor: McpCursor | None = None,
        limit: McpPageLimit = 50,
    ) -> KnowledgeScopePageV1:
        try:
            return application.list_scopes(
                raw_token=_raw_token(ctx), cursor=cursor, limit=limit
            )
        except ToolError:
            _mark_business_audited(ctx)
            raise

    @server.tool(name="atlas.research", structured_output=True)
    def research(
        ctx: Context,
        question: McpQuestion,
        idempotency_key: McpIdempotencyKey,
        scope: AgentResearchScopeV1,
        output_mode: Literal[
            "evidence_packet", "evidence_packet_and_answer"
        ] = "evidence_packet",
    ) -> ResearchAcceptedV1:
        try:
            payload = StartAgentResearchV1(
                question=question,
                idempotency_key=idempotency_key,
                scope=scope,
                output_mode=output_mode,
            )
        except ValidationError as exc:
            raise ToolError("invalid_research_request") from exc
        try:
            return application.start(raw_token=_raw_token(ctx), payload=payload)
        except ToolError:
            _mark_business_audited(ctx)
            raise

    @server.tool(name="atlas.get_research", structured_output=True)
    def get_research(ctx: Context, research_id: McpIdentity) -> ResearchStatusV1:
        try:
            return application.get(raw_token=_raw_token(ctx), research_id=research_id)
        except ToolError:
            _mark_business_audited(ctx)
            raise

    @server.tool(name="atlas.read_evidence", structured_output=True)
    def read_evidence(
        ctx: Context,
        research_id: McpIdentity,
        evidence_id: McpIdentity,
        representation: Literal["text", "visual", "native"],
    ) -> EvidenceContentV1:
        try:
            return application.read_evidence(
                raw_token=_raw_token(ctx),
                research_id=research_id,
                evidence_id=evidence_id,
                representation=representation,
            )
        except ToolError:
            _mark_business_audited(ctx)
            raise

    streamable = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=False,
        transport_security=config.transport_security,
    )
    protected = AgentBearerMiddleware(
        streamable,
        access=access,
        audit_writer=audit_writer,
    )
    return AtlasMcpTransport(server=server, asgi_app=protected, config=config)


__all__ = [
    "AgentResearchEvidenceReader",
    "AtlasMcpResearchApplication",
    "AtlasMcpTransport",
    "EvidenceContentV1",
    "KnowledgeScopePageV1",
    "McpExactPathMiddleware",
    "McpBusinessError",
    "PostgresMcpAgentAccess",
    "ResearchAcceptedV1",
    "ResearchStatusV1",
    "build_mcp_transport",
]
