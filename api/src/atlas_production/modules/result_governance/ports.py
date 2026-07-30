from __future__ import annotations

from typing import Protocol

from .contracts import GovernedResult, ResultSurface


class ResultGovernanceRuntime(Protocol):
    def build_messages(
        self,
        query_text: str,
        evidence: list[EvidenceRecord],
    ) -> list[dict[str, str]]: ...

    def govern_policy_denial(self, surface: ResultSurface) -> GovernedResult: ...

    def govern_conversation_missing_evidence(self) -> GovernedResult: ...

    def govern_model_route_unavailable(
        self,
        surface: ResultSurface,
    ) -> GovernedResult: ...

    def govern_provider_failure(
        self,
        surface: ResultSurface,
        *,
        error_code: str,
        message_code: str,
    ) -> GovernedResult: ...

    def govern_provider_answer(
        self,
        surface: ResultSurface,
        answer_text: str,
    ) -> GovernedResult: ...

    def govern_citation_validation_failure(self) -> GovernedResult: ...
