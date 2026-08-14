from __future__ import annotations

from .api_models import (
    ReadinessState,
)
from .ports import OpsReadinessRepository


class OpsReadinessService:
    def __init__(self, repository: OpsReadinessRepository, artifact_storage=None, notes_collaboration=None) -> None:
        self.repository = repository
        self.artifact_storage = artifact_storage
        self.notes_collaboration = notes_collaboration

    def readiness(self) -> ReadinessState:
        self.repository.refresh()
        blockers: list[str] = []
        if self.artifact_storage is not None and not self.artifact_storage.readiness_available():
            blockers.append("ops.configure_artifact_storage")
        if self.notes_collaboration is not None and not self.notes_collaboration.readiness_available():
            blockers.append("ops.notes_collaboration_is_unavailable")
        if not self.repository.has_projects():
            blockers.append("ops.create_project")
        if not self.repository.has_active_permission():
            blockers.append("ops.grant_active_project_permission")
        evidence_ready_projects = self.repository.evidence_ready_project_ids()
        if not evidence_ready_projects:
            blockers.append("ops.prepare_searchable_evidence")
        if not self.repository.processing_runner_available():
            blockers.append("ops.processing_runner_is_unavailable")
        if not self.repository.credential_encryption_available():
            blockers.append('provider.credential_encryption_is_unavailable')
        elif not self.repository.has_tested_model_route():
            blockers.append("ops.configure_and_test_model_route")
        ready = not blockers
        return ReadinessState(
            ready=ready,
            health="ok" if ready else "degraded",
            setup_blockers=blockers,
            evidence_ready_projects=evidence_ready_projects,
            message_code='workspace.is_ready' if ready else 'common.setup_is_incomplete',
        )
