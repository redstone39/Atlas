from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from atlas_production.modules.audit.public import (
    TurnAuditDraftV2,
    audit_event_status,
)
from atlas_production.modules.citation_preview.public import CitationBindingDraftV2
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.result_governance.public import GovernedAnswerDraftV2
from atlas_production.modules.turn_runtime.public import (
    ExecutionState,
    TerminalOutcomeV1,
)
from atlas_production.shared.public import AuditEventRecord

from .admin_api_models import (
    AcceptedResearchAuditListItemV1,
    AgentResearchAdminAnswerV1,
    AgentResearchAuditDetailV1,
    AgentResearchAuditListItemV1,
    AgentResearchAuditListV1,
    AgentResearchEvidenceContentV1,
    AgentResearchRuntimeDetailV1,
    DeniedResearchAuditListItemV1,
)
from .contracts import AgentResearchAuditSummaryV1, AgentResearchRecordV1
from .ports import AgentResearchStore


_BUSINESS_EVENT_TYPES = frozenset(
    {"agent_research_accepted", "agent_research_replayed"}
)


@dataclass(frozen=True, slots=True)
class AgentResearchAuditError(Exception):
    error_code: str
    message_code: str
    status_code: int


class AgentResearchAuditEventReader(Protocol):
    def denials(
        self,
        *,
        after: tuple[datetime, str] | None,
        upper: tuple[datetime, str] | None,
        limit: int,
    ) -> list[AuditEventRecord]: ...

    def timeline(
        self, *, research_id: str, limit: int = 200
    ) -> list[AuditEventRecord]: ...


class AgentResearchRuntimeReader(Protocol):
    def find_execution(self, execution_id: str): ...

    def terminal_outcome(self, execution_id: str) -> TerminalOutcomeV1 | None: ...

    def events_bounded(self, execution_id: str, *, limit: int): ...


class AgentResearchAdminAuditWriter(Protocol):
    def append_read_audit(
        self,
        event_type: str,
        *,
        actor_id: str | None,
        target_ref: str | None,
        project_id: str | None,
        message_code: str,
        metadata: dict[str, object],
    ) -> object: ...





class AgentResearchAdminEvidenceReader(Protocol):
    def validate_completed(
        self,
        *,
        record: AgentResearchRecordV1,
        terminal: TerminalOutcomeV1,
    ) -> tuple[
        TurnAuditDraftV2,
        GovernedAnswerDraftV2 | None,
        CitationBindingDraftV2 | None,
    ]: ...

    def read_admin(
        self,
        *,
        record: AgentResearchRecordV1,
        evidence_id: str,
        representation: Literal["text", "visual", "native"],
    ) -> AgentResearchEvidenceContentV1: ...


class AgentResearchEvidenceUnavailable(RuntimeError):
    pass


class AgentResearchEvidenceProjectionIncomplete(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _AuditCursor:
    accepted_after: tuple[datetime, str] | None
    denied_after: tuple[datetime, str] | None
    accepted_upper: tuple[datetime, str] | None
    denied_upper: tuple[datetime, str] | None

@dataclass(frozen=True, slots=True)
class AgentResearchAuditService:
    researches: AgentResearchStore
    audit_events: AgentResearchAuditEventReader
    audit_writer: AgentResearchAdminAuditWriter
    runtime: AgentResearchRuntimeReader
    evidence: AgentResearchAdminEvidenceReader

    @staticmethod
    def _admin(actor: UserRecord | None) -> UserRecord:
        if actor is None:
            raise AgentResearchAuditError(
                "unauthenticated",
                "auth.please_sign_in_before_using_admin_tools",
                401,
            )
        if not actor.active or actor.system_role != "admin":
            raise AgentResearchAuditError(
                "access_denied",
                "permission.admin_permission_is_required",
                403,
            )
        return actor

    def _audit(
        self,
        actor: UserRecord,
        event_type: str,
        target_ref: str,
        message_code: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.audit_writer.append_read_audit(
            event_type,
            actor_id=actor.actor_id,
            target_ref=target_ref,
            project_id=None,
            message_code=message_code,
            metadata={
                "admin_global_history_access": True,
                **(metadata or {}),
            },
        )

    def list_admin(
        self,
        actor: UserRecord | None,
        *,
        cursor: str | None,
        limit: int,
    ) -> AgentResearchAuditListV1:
        admin = self._admin(actor)
        position = self._decode_cursor(cursor)
        self._audit(
            admin,
            "read_agent_research_audit",
            "agent-research:*",
            "audit.admin_listed_agent_research",
        )
        if position is None:
            accepted = self.researches.list_audit_summaries(
                after=None,
                upper=None,
                limit=limit + 1,
            )
            denied = self.audit_events.denials(
                after=None,
                upper=None,
                limit=limit + 1,
            )
            accepted_upper = (
                None
                if not accepted
                else (accepted[0].accepted_at, accepted[0].research_id)
            )
            denied_upper = (
                None
                if not denied
                else (self._event_time(denied[0]), denied[0].event_id)
            )
            accepted_after = None
            denied_after = None
        else:
            accepted_upper = position.accepted_upper
            denied_upper = position.denied_upper
            accepted_after = position.accepted_after
            denied_after = position.denied_after
            accepted = (
                []
                if accepted_upper is None
                else self.researches.list_audit_summaries(
                    after=accepted_after,
                    upper=accepted_upper,
                    limit=limit + 1,
                )
            )
            denied = (
                []
                if denied_upper is None
                else self.audit_events.denials(
                    after=denied_after,
                    upper=denied_upper,
                    limit=limit + 1,
                )
            )
        candidates: list[
            tuple[datetime, str, AgentResearchAuditListItemV1]
        ] = [
            (
                item.accepted_at,
                f"accepted:{item.research_id}",
                self._accepted_item(item),
            )
            for item in accepted
        ]
        candidates.extend(
            (
                self._event_time(event),
                f"denied:{event.event_id}",
                self._denied_item(event),
            )
            for event in denied
        )
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        page = candidates[:limit]
        next_accepted = accepted_after
        next_denied = denied_after
        for occurred_at, _, item in page:
            if isinstance(item, AcceptedResearchAuditListItemV1):
                next_accepted = (occurred_at, item.research_id)
            else:
                next_denied = (occurred_at, item.event_id)
        next_cursor = None
        if len(candidates) > limit:
            next_cursor = self._encode_cursor(
                _AuditCursor(
                    accepted_after=next_accepted,
                    denied_after=next_denied,
                    accepted_upper=accepted_upper,
                    denied_upper=denied_upper,
                )
            )
        return AgentResearchAuditListV1(
            items=[item for _, _, item in page],
            next_cursor=next_cursor,
        )

    def get_admin(
        self,
        actor: UserRecord | None,
        research_id: str,
    ) -> AgentResearchAuditDetailV1:
        admin = self._admin(actor)
        self._audit(
            admin,
            "read_agent_research_audit_detail",
            f"agent-research:{research_id}",
            "audit.admin_opened_agent_research",
        )
        record = self._record(research_id)
        terminal = self.runtime.terminal_outcome(record.execution_id)
        audit = None
        governed = None
        citations = None
        if record.status == "completed":
            if terminal is None:
                raise AgentResearchAuditError(
                    "projection_incomplete", "common.rejected", 503
                )
            audit, governed, citations = self._validate_completed(
                record,
                terminal,
            )
        answer = self._answer(
            record,
            terminal,
            audit,
            governed,
            citations,
        )
        events = self.audit_events.timeline(
            research_id=research_id,
            limit=200,
        )
        return AgentResearchAuditDetailV1(
            research_id=record.research_id,
            execution_id=record.execution_id,
            actor_id=record.actor_id,
            question=record.question,
            accepted_scope=record.snapshot.scope,
            output_mode=record.output_mode,
            status=record.status,
            packet=record.packet,
            answer=answer,
            business_events=[
                audit_event_status(event)
                for event in events
                if event.event_type in _BUSINESS_EVENT_TYPES
            ],
            accepted_at=record.accepted_at,
            completed_at=record.completed_at,
        )

    def get_runtime(
        self,
        actor: UserRecord | None,
        research_id: str,
    ) -> AgentResearchRuntimeDetailV1:
        admin = self._admin(actor)
        self._audit(
            admin,
            "read_agent_research_runtime",
            f"agent-research:{research_id}",
            "audit.admin_opened_agent_research_runtime",
        )
        record = self._record(research_id)
        snapshot = self.runtime.find_execution(record.execution_id)
        if (
            snapshot is None
            or snapshot.result_kind != "agent_research"
            or snapshot.research_id != record.research_id
            or snapshot.execution_id != record.execution_id
        ):
            raise AgentResearchAuditError(
                "projection_incomplete", "common.rejected", 503
            )
        audit_steps = []
        terminal = self.runtime.terminal_outcome(record.execution_id)
        if terminal is not None and terminal.execution_id != record.execution_id:
            raise AgentResearchAuditError(
                "projection_incomplete", "common.rejected", 503
            )
        if snapshot.state is ExecutionState.TERMINAL_COMPLETED:
            if (
                record.status != "completed"
                or terminal is None
                or terminal.outcome != "completed"
                or terminal.result_kind != "agent_research"
            ):
                raise AgentResearchAuditError(
                    "projection_incomplete", "common.rejected", 503
                )
            audit_steps = self._validate_completed(record, terminal)[0].steps
        elif record.status == "completed":
            raise AgentResearchAuditError(
                "projection_incomplete", "common.rejected", 503
            )
        elif snapshot.state is ExecutionState.TERMINAL_FAILED:
            if (
                terminal is None
                or terminal.outcome != "failed"
                or terminal.failure_code != snapshot.terminal_failure_code
            ):
                raise AgentResearchAuditError(
                    "projection_incomplete", "common.rejected", 503
                )
        elif terminal is not None:
            raise AgentResearchAuditError(
                "projection_incomplete", "common.rejected", 503
            )
        runtime_events = self.runtime.events_bounded(
            record.execution_id,
            limit=201,
        )
        return AgentResearchRuntimeDetailV1(
            research_id=record.research_id,
            execution_id=record.execution_id,
            state=snapshot.state,
            version=snapshot.version,
            reasoning_mode=snapshot.reasoning_mode,
            reasoning_trace=snapshot.reasoning_trace,
            prompt_skill_catalogs=snapshot.prompt_skill_catalogs,
            prompt_skill_selections=snapshot.prompt_skill_selections,
            failure_code=snapshot.terminal_failure_code,
            budget=snapshot.budget,
            events=runtime_events[:200],
            events_truncated=len(runtime_events) > 200,
            audit_steps=audit_steps,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
        )

    def read_evidence(
        self,
        actor: UserRecord | None,
        research_id: str,
        evidence_id: str,
        representation: Literal["text", "visual", "native"],
    ) -> AgentResearchEvidenceContentV1:
        admin = self._admin(actor)
        self._audit(
            admin,
            "read_agent_research_evidence",
            f"agent-research:{research_id}",
            "audit.admin_opened_agent_research_evidence",
            metadata={
                "evidence_id": evidence_id,
                "representation": representation,
            },
        )
        record = self._record(research_id)
        if record.packet is None:
            raise AgentResearchAuditError(
                "not_found",
                "audit.agent_research_evidence_was_not_found",
                404,
            )
        try:
            return self.evidence.read_admin(
                record=record,
                evidence_id=evidence_id,
                representation=representation,
            )
        except AgentResearchEvidenceProjectionIncomplete as exc:
            raise AgentResearchAuditError(
                "projection_incomplete", "common.rejected", 503
            ) from exc
        except AgentResearchEvidenceUnavailable as exc:
            raise AgentResearchAuditError(
                "not_found",
                "audit.agent_research_evidence_was_not_found",
                404,
            ) from exc

    def _validate_completed(
        self,
        record: AgentResearchRecordV1,
        terminal: TerminalOutcomeV1,
    ) -> tuple[
        TurnAuditDraftV2,
        GovernedAnswerDraftV2 | None,
        CitationBindingDraftV2 | None,
    ]:
        try:
            return self.evidence.validate_completed(
                record=record,
                terminal=terminal,
            )
        except AgentResearchEvidenceProjectionIncomplete as exc:
            raise AgentResearchAuditError(
                "projection_incomplete", "common.rejected", 503
            ) from exc

    def _record(self, research_id: str) -> AgentResearchRecordV1:
        record = self.researches.find(research_id)
        if record is None:
            raise AgentResearchAuditError(
                "not_found", "audit.agent_research_was_not_found", 404
            )
        return record

    def _answer(
        self,
        record: AgentResearchRecordV1,
        terminal: TerminalOutcomeV1 | None,
        audit: TurnAuditDraftV2 | None,
        governed: GovernedAnswerDraftV2 | None,
        citations: CitationBindingDraftV2 | None,
    ) -> AgentResearchAdminAnswerV1 | None:
        if record.status != "completed":
            return None
        if (
            record.packet_ref is None
            or record.packet_digest is None
            or terminal is None
            or terminal.execution_id != record.execution_id
            or terminal.outcome != "completed"
            or terminal.result_kind != "agent_research"
            or terminal.research_packet_ref != record.packet_ref
            or terminal.research_packet_digest != record.packet_digest
            or audit is None
        ):
            raise AgentResearchAuditError(
                "projection_incomplete", "common.rejected", 503
            )
        if record.output_mode == "evidence_packet":
            return AgentResearchAdminAnswerV1(
                status="not_requested",
                packet_ref=record.packet_ref,
                packet_digest=record.packet_digest,
            )
        if (
            terminal.governed_answer_draft_ref is None
            or terminal.citation_binding_draft_ref is None
        ):
            return AgentResearchAdminAnswerV1(
                status="unavailable",
                packet_ref=record.packet_ref,
                packet_digest=record.packet_digest,
            )
        if governed is None or citations is None:
            raise AgentResearchAuditError(
                "projection_incomplete", "common.rejected", 503
            )
        return AgentResearchAdminAnswerV1(
            status="available",
            packet_ref=record.packet_ref,
            packet_digest=record.packet_digest,
            governed_answer=governed,
            citations=citations,
        )

    @staticmethod
    def _accepted_item(
        item: AgentResearchAuditSummaryV1,
    ) -> AcceptedResearchAuditListItemV1:
        return AcceptedResearchAuditListItemV1(
            research_id=item.research_id,
            execution_id=item.execution_id,
            actor_id=item.actor_id,
            status=item.status,
            output_mode=item.output_mode,
            occurred_at=item.accepted_at,
            completed_at=item.completed_at,
        )

    @staticmethod
    def _denied_item(
        event: AuditEventRecord,
    ) -> DeniedResearchAuditListItemV1:
        reason = event.metadata.get("reason")
        return DeniedResearchAuditListItemV1(
            event_id=event.event_id,
            actor_id=event.actor_id,
            message_code=event.message_code,
            reason=reason if isinstance(reason, str) and reason else "denied",
            occurred_at=AgentResearchAuditService._event_time(event),
        )

    @staticmethod
    def _event_time(event: AuditEventRecord) -> datetime:
        timestamp = event.created_at
        parsed = (
            datetime.fromisoformat(timestamp)
            if isinstance(timestamp, str)
            else timestamp
        )
        if parsed.tzinfo is None:
            raise ValueError("audit event timestamp must be timezone-aware")
        return parsed

    @staticmethod
    def _encode_cursor(cursor: _AuditCursor) -> str:
        def part(value: tuple[datetime, str] | None):
            return None if value is None else [value[0].isoformat(), value[1]]

        raw = json.dumps(
            {
                "accepted_after": part(cursor.accepted_after),
                "accepted_upper": part(cursor.accepted_upper),
                "denied_after": part(cursor.denied_after),
                "denied_upper": part(cursor.denied_upper),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> _AuditCursor | None:
        if cursor is None:
            return None
        try:
            if not cursor or len(cursor) > 1024:
                raise ValueError
            value = json.loads(
                base64.urlsafe_b64decode(
                    cursor + "=" * (-len(cursor) % 4)
                ).decode("utf-8")
            )
            if not isinstance(value, dict) or set(value) != {
                "accepted_after",
                "accepted_upper",
                "denied_after",
                "denied_upper",
            }:
                raise ValueError

            def parse(part: object) -> tuple[datetime, str] | None:
                if part is None:
                    return None
                if (
                    not isinstance(part, list)
                    or len(part) != 2
                    or not all(isinstance(item, str) and item for item in part)
                ):
                    raise ValueError
                timestamp = datetime.fromisoformat(part[0])
                if timestamp.tzinfo is None:
                    raise ValueError
                return timestamp, part[1]

            result = _AuditCursor(
                accepted_after=parse(value["accepted_after"]),
                accepted_upper=parse(value["accepted_upper"]),
                denied_after=parse(value["denied_after"]),
                denied_upper=parse(value["denied_upper"]),
            )
            if (
                result.accepted_after is not None
                and result.accepted_upper is None
            ) or (
                result.denied_after is not None
                and result.denied_upper is None
            ):
                raise ValueError
            return result
        except (
            binascii.Error,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            raise AgentResearchAuditError(
                "invalid_cursor",
                "audit.agent_research_cursor_is_invalid",
                400,
            ) from None
