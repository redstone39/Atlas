"""Feature-private deterministic helpers for Workspace turn coordination."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from atlas_production.modules.context_engineering.public import (
    ContextExchangeV3,
    ContextLineageEdgeV3,
    ContextSummaryInputV4,
    MaterializeContextPackV3,
    ModelUserInputV3,
    ModelUserTextSegmentV3,
)
from atlas_production.modules.conversation.public import ConversationTurnMemberV1
from atlas_production.modules.turn_runtime.public import (
    ExecutionState,
    turn_route_snapshots as route_snapshots,
)


def stable_id(kind: str, actor_id: str, key: str) -> str:
    return f"{kind}-{uuid5(NAMESPACE_URL, f'atlas:{kind}:{actor_id}:{key}')}"


def context_ref(execution_id: str) -> str:
    return f"context-pack-{hashlib.sha256(execution_id.encode()).hexdigest()}"


def input_projection_ref(execution_id: str) -> str:
    return f"input-projection-{hashlib.sha256(execution_id.encode()).hexdigest()}"


def historical_exchange_content_digest(
    *, user_text: str, assistant_text: str, direct_document_ids: list[str]
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "user_text": user_text,
                "assistant_text": assistant_text,
                "assistant_authority": (
                    None
                    if not assistant_text
                    else {
                        "authority": "pending_verification",
                        "usage_scope": "dialogue_context_only",
                    }
                ),
                "direct_document_ids": direct_document_ids,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def acceptance_identity(
    *, actor_id: str, conversation_id: str, operation: str,
    retry_of_turn_id: str | None, idempotency_key: str, input_text: str,
) -> tuple[str, str, str, str]:
    identity_key = ":".join(
        [conversation_id, operation, retry_of_turn_id or "none", idempotency_key]
    )
    return (
        stable_id("turn", actor_id, identity_key),
        stable_id("execution", actor_id, identity_key),
        stable_id("carrier", actor_id, identity_key),
        hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
    )




def acceptance_failure_code(error: Exception) -> str:
    safe_code = getattr(error, "safe_code", None)
    if safe_code in {
        "summary_generation_failed", "context_limit_exceeded",
        "resolver_failed", "rewrite_failed",
    }:
        return str(safe_code)
    return "contract_violation"


def conversation_turn_status(state: ExecutionState) -> str:
    if state is ExecutionState.TERMINAL_COMPLETED:
        return "completed"
    if state is ExecutionState.TERMINAL_FAILED:
        return "failed_closed"
    return "processing"


def logical_member_chains(
    members: Sequence[ConversationTurnMemberV1],
    retry_sources: Mapping[str, str],
    *, current_turn_id: str,
) -> list[tuple[str, list[ConversationTurnMemberV1]]]:
    def root_turn_id(turn_id: str) -> str:
        seen: set[str] = set()
        current = turn_id
        while current not in seen:
            seen.add(current)
            source = retry_sources.get(current)
            if source is None:
                return current
            current = source
        return turn_id

    current_source = retry_sources.get(current_turn_id)
    excluded_root = root_turn_id(current_source) if current_source else None
    chains: dict[str, list[ConversationTurnMemberV1]] = {}
    for member in sorted(members, key=lambda item: item.ordinal):
        if member.turn_id == current_turn_id:
            continue
        root = root_turn_id(member.turn_id)
        if root != excluded_root:
            chains.setdefault(root, []).append(member)
    return [(root_turn_id(chain[0].turn_id), chain) for chain in chains.values()]


def logical_members(
    chains: Sequence[tuple[str, Sequence[ConversationTurnMemberV1]]],
    states: Mapping[str, ExecutionState],
) -> list[tuple[str, ConversationTurnMemberV1]]:
    selected: list[tuple[str, ConversationTurnMemberV1]] = []
    for logical_turn_id, chain in chains:
        completed = [
            member for member in chain
            if states[member.execution_id] is ExecutionState.TERMINAL_COMPLETED
        ]
        representative = (completed or chain)[-1]
        selected.append((logical_turn_id, representative))
    return selected


def build_context_command(
    *, execution_id: str, actor_id: str, conversation_id: str, turn_id: str,
    input_text: str, recent: list[ContextExchangeV3],
    summary: ContextSummaryInputV4 | None, context_token_budget: int,
) -> MaterializeContextPackV3:
    pack_ref = context_ref(execution_id)
    edges = [
        ContextLineageEdgeV3(
            dependent_turn_id=turn_id,
            dependent_context_pack_ref=pack_ref,
            source_turn_id=item.representative_turn_id,
            source_resource_kind="turn",
            dependency_kind="recent_turn",
        )
        for item in recent
    ]
    if summary is not None:
        edges.extend(
            ContextLineageEdgeV3(
                dependent_turn_id=turn_id,
                dependent_context_pack_ref=pack_ref,
                source_turn_id=source.representative_turn_id,
                source_resource_ref=summary.summary_ref,
                source_resource_kind="summary",
                dependency_kind="summary_source",
            )
            for source in summary.sources
        )
    return MaterializeContextPackV3(
        context_pack_ref=pack_ref,
        execution_id=execution_id,
        input_projection_ref=input_projection_ref(execution_id),
        conversation_id=conversation_id,
        dependent_turn_id=turn_id,
        model_user_input=ModelUserInputV3(
            content_segments=[ModelUserTextSegmentV3(text=input_text)]
        ),
        recent_tail=recent,
        summary=summary,
        source_lineage=edges,
        token_budget=context_token_budget,
        idempotency_key=stable_id("context", actor_id, execution_id),
    )


def terminal_projection_is_complete(
    *, execution_id: str, answer: Any, binding: Any,
    audit: Any, evidence_pack: Any,
) -> bool:
    return not (
        answer is None or binding is None or audit is None
        or audit.execution_id != execution_id or evidence_pack is None
        or evidence_pack.execution_id != execution_id
        or binding.execution_id != execution_id
        or binding.governed_answer_draft_ref != answer.draft_ref
        or binding.governed_answer_digest != answer.digest
        or audit.evidence_pack_ref != evidence_pack.evidence_pack_ref
        or audit.evidence_pack_digest != evidence_pack.digest
        or audit.governed_answer_draft_ref != answer.draft_ref
        or audit.governed_answer_digest != answer.digest
        or audit.citation_binding_draft_ref != binding.draft_ref
        or audit.citation_binding_digest != binding.digest
        or audit.evidence_review_status != answer.evidence_review_status
    )


def reasoning_progress_payload(event: Any) -> dict[str, Any] | None:
    if event.event_type != "reasoning_progressed":
        return None
    if event.reasoning_phase is None or event.progress_status is None or event.message_code is None:
        raise ValueError("incomplete reasoning progress")
    return {
        "event_id": event.event_id, "sequence": event.sequence,
        "phase": event.reasoning_phase, "status": event.progress_status,
        "cycle": event.cycle, "message_code": event.message_code,
        "message_params": event.message_params, "created_at": event.created_at,
    }
