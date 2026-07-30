from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.document_intake import (
    AtlasDocumentRow,
    AtlasDocumentTagRow,
)
from atlas_production.infrastructure.persistence.identity_access import (
    AtlasPermissionGrantRow,
)
from atlas_production.infrastructure.persistence.model_routing import (
    runtime_joined_snapshot,
)
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasEvidenceRow,
)
from atlas_production.infrastructure.persistence.project_governance import (
    AtlasProjectRow,
)


SessionFactory = Callable[[], Session]
class ProcessingRunnerAvailabilityPort(Protocol):
    def available(self) -> bool: ...


class CredentialEncryptionAvailabilityPort(Protocol):
    def available(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class PostgresOpsReadinessRepository:
    """Bounded durable readiness reads plus separately injected probes."""

    session_factory: SessionFactory
    processing_runner: ProcessingRunnerAvailabilityPort
    credential_encryption: CredentialEncryptionAvailabilityPort

    def refresh(self) -> None:
        return None

    def has_projects(self) -> bool:
        with self.session_factory() as session:
            return (
                session.scalar(
                    select(AtlasProjectRow.project_id).limit(1)
                )
                is not None
            )

    def has_active_permission(self) -> bool:
        with self.session_factory() as session:
            return (
                session.scalar(
                    select(AtlasPermissionGrantRow.grant_id)
                    .where(
                        AtlasPermissionGrantRow.status == "active",
                        AtlasPermissionGrantRow.effect == "allow",
                    )
                    .limit(1)
                )
                is not None
            )

    def evidence_ready_project_ids(self) -> list[str]:
        with self.session_factory() as session:
            return list(
                session.execute(
                    select(AtlasDocumentTagRow.tag_id)
                    .join(
                        AtlasDocumentRow,
                        AtlasDocumentRow.document_id
                        == AtlasDocumentTagRow.document_id,
                    )
                    .join(
                        AtlasEvidenceRow,
                        AtlasEvidenceRow.document_id
                        == AtlasDocumentRow.document_id,
                    )
                    .where(
                        AtlasDocumentTagRow.tag_type == "project",
                        AtlasDocumentRow.lifecycle_status == "active",
                        AtlasEvidenceRow.status == "ready",
                        AtlasEvidenceRow.processing_generation
                        == AtlasDocumentRow.active_processing_generation,
                    )
                    .distinct()
                    .order_by(AtlasDocumentTagRow.tag_id)
                ).scalars().all()
            )

    def has_tested_model_route(self) -> bool:
        with self.session_factory() as session:
            return runtime_joined_snapshot(session) is not None

    def processing_runner_available(self) -> bool:
        return self.processing_runner.available()

    def credential_encryption_available(self) -> bool:
        return self.credential_encryption.available()


__all__ = [
    "CredentialEncryptionAvailabilityPort",
    "PostgresOpsReadinessRepository",
    "ProcessingRunnerAvailabilityPort",
]
