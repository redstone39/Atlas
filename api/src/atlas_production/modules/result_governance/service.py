from __future__ import annotations

from atlas_production.modules.processing_pipeline.records import (
    EvidenceRecord,
)
from atlas_production.shared.public import (
    content_digest,
)
from .contracts import (
    GovernedResult,
    ResultSurface,
)


class ResultGovernanceService:
    def build_messages(
        self,
        query_text: str,
        evidence: list[EvidenceRecord],
    ) -> list[dict[str, str]]:
        evidence_text = "\n\n".join(
            f"[{index}] {item.document_title} | {item.locator_label}\n{item.snippet}\n{item.content}"
            for index, item in enumerate(evidence, start=1)
        )
        evidence_pack_digest = content_digest(evidence_text)
        return [
            {
                "role": "system",
                "content": (
                    "You are Atlas, an engineering knowledge answer engine. "
                    "Answer only from the provided validated evidence. "
                    "If the evidence is insufficient, start the response with 'REFUSE:'."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question:\n{query_text}\n\n"
                    f"Validated evidence pack ({evidence_pack_digest}):\n{evidence_text}"
                ),
            },
        ]

    def govern_policy_denial(self, surface: ResultSurface) -> GovernedResult:
        self._require_surface(surface)
        return GovernedResult(
            status="failed_closed",
            answer_text=None,
            refusal_code="policy_denied",
            user_reason="result.knowledge_scope_access_required",
        )

    def govern_conversation_missing_evidence(self) -> GovernedResult:
        return GovernedResult(
            status="unknown",
            answer_text=None,
            refusal_code="missing_evidence",
            user_reason="result.validated_evidence_is_not_available",
        )

    def govern_model_route_unavailable(
        self,
        surface: ResultSurface,
    ) -> GovernedResult:
        self._require_surface(surface)
        return GovernedResult(
            status="failed_closed",
            answer_text=None,
            refusal_code="model_route_unavailable",
            user_reason="result.tested_model_route_is_required",
        )

    def govern_provider_failure(
        self,
        surface: ResultSurface,
        *,
        error_code: str,
        message_code: str,
    ) -> GovernedResult:
        self._require_surface(surface)
        return GovernedResult(
            status="failed_closed",
            answer_text=None,
            refusal_code=error_code,
            user_reason='provider.invocation_failed',
        )

    def govern_provider_answer(
        self,
        surface: ResultSurface,
        answer_text: str,
    ) -> GovernedResult:
        self._require_surface(surface)
        normalized = answer_text.strip()
        if normalized.upper().startswith("REFUSE:"):
            return GovernedResult(
                status="unknown",
                answer_text=None,
                refusal_code="provider_refused",
                user_reason="result.provider_refused",
            )
        return GovernedResult(
            status="answered",
            answer_text=normalized,
            refusal_code=None,
            user_reason="result.answered_from_validated_evidence",
        )

    def govern_citation_validation_failure(self) -> GovernedResult:
        return GovernedResult(
            status="failed_closed",
            answer_text=None,
            refusal_code="citation_validation_failed",
            user_reason="result.evidence_validation_failed",
        )

    @staticmethod
    def _require_surface(surface: ResultSurface) -> None:
        if surface != "conversation":
            raise ValueError(f"Unsupported result surface: {surface}")
