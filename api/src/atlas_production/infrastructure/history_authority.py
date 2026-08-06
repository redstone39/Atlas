"""Pure model-wire projection for bounded historical conversation authority."""

from __future__ import annotations


HISTORY_AUTHORITY_POLICY = {
    "enforcement": "soft",
    "historical_user_context": {
        "authority": "user_provided_history",
        "allowed_use": "user premise and dialogue context",
    },
    "historical_assistant_context": {
        "authority": "pending_verification",
        "usage_scope": "dialogue_context_only",
        "allowed_use": (
            "resolve referents, entity names, dialogue intent, continuity, and goals"
        ),
        "evidence_status": "not_factual_evidence",
    },
    "material_factual_reuse": (
        "A material factual claim sourced only from pending assistant history requires "
        "current authorized evidence before it is reused as fact."
    ),
    "soft_behavior": (
        "This policy guides model behavior only. It does not reject the execution, "
        "force a retry, or create a hard gate."
    ),
}


def history_exchange_payload(
    *, user_text: str, assistant_text: str | None
) -> dict[str, object]:
    return {
        "user_message": {
            "text": user_text,
            "authority": "user_provided_history",
        },
        "assistant_message": (
            None
            if assistant_text is None
            else {
                "text": assistant_text,
                "authority": "pending_verification",
                "usage_scope": "dialogue_context_only",
            }
        ),
    }


def history_summary_payload(
    *,
    historical_user_context: str,
    assistant_pending_verification_context: str,
) -> dict[str, object]:
    return {
        "historical_user_context": {
            "text": historical_user_context,
            "authority": "user_provided_history",
        },
        "assistant_pending_verification_context": {
            "text": assistant_pending_verification_context,
            "authority": "pending_verification",
            "usage_scope": "dialogue_context_only",
        },
    }


__all__ = [
    "HISTORY_AUTHORITY_POLICY",
    "history_exchange_payload",
    "history_summary_payload",
]
