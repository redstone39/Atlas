from __future__ import annotations

from dataclasses import dataclass

from atlas_production.infrastructure.postgres_owner.ops import (
    PostgresOpsReadinessRepository,
)


@dataclass(frozen=True, slots=True)
class PostgresOpsAdapter:
    owner: PostgresOpsReadinessRepository

    def refresh(self) -> None:
        self.owner.refresh()

    def credential_encryption_available(self) -> bool:
        return self.owner.credential_encryption_available()

    def has_projects(self) -> bool:
        return self.owner.has_projects()

    def has_active_permission(self) -> bool:
        return self.owner.has_active_permission()

    def evidence_ready_project_ids(self) -> list[str]:
        return self.owner.evidence_ready_project_ids()

    def has_tested_model_route(self) -> bool:
        return self.owner.has_tested_model_route()

    def processing_runner_available(self) -> bool:
        return self.owner.processing_runner_available()


__all__ = ["PostgresOpsAdapter"]
