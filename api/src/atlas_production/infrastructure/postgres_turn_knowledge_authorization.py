"""Private authorization projections for production turn knowledge."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from atlas_production.modules.authorization.public import (
    CurrentGrantAuthorizationSnapshotV1,
    CurrentResourceAuthorizationSnapshotV1,
    GrantDocumentResourceV1,
)

from atlas_production.infrastructure.postgres_turn_knowledge_contracts import (
    CurrentDocumentResource,
    ProductionKnowledgeRowSource,
    _digest,
)


class ProductionCurrentResourceAuthorizationReader:
    def __init__(self, rows: ProductionKnowledgeRowSource) -> None:
        self._rows = rows

    def current_grant_authorization(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        deadline_at: datetime | None = None,
    ) -> CurrentGrantAuthorizationSnapshotV1:
        state = self._rows.grant_authority(
            actor_id=actor_id,
            conversation_id=conversation_id,
            deadline_at=deadline_at,
        )
        return CurrentGrantAuthorizationSnapshotV1(**asdict(state))

    def current_resource_authorizations(
        self,
        *,
        actor_id: str,
        resource_refs: tuple[str, ...],
        deadline_at: datetime | None = None,
    ) -> tuple[CurrentResourceAuthorizationSnapshotV1, ...]:
        states = {
            state.resource_ref: state
            for state in self._rows.resource_authorizations(
                actor_id=actor_id,
                resource_refs=resource_refs,
                deadline_at=deadline_at,
            )
        }
        digest = _digest(
            [
                {
                    "resource_ref": ref,
                    "authorized": bool(states.get(ref) and states[ref].authorized),
                    "current": (
                        None
                        if states.get(ref) is None or states[ref].document is None
                        else asdict(states[ref].document)
                    ),
                }
                for ref in sorted(set(resource_refs))
            ]
        )
        revision = int(digest[:15], 16) + 1
        snapshots = []
        for ref in resource_refs:
            state = states.get(ref)
            document = None if state is None else state.document
            snapshots.append(
                CurrentResourceAuthorizationSnapshotV1(
                    actor_id=actor_id,
                    resource_ref=ref,
                    authorization_revision=revision,
                    snapshot_ref=f"resource-authority-{digest}",
                    authorized=bool(state and state.authorized),
                    active=document is not None,
                    lifecycle_epoch=None if document is None else document.lifecycle_epoch,
                    version_ref=None if document is None else document.document_version_ref,
                    generation_ref=None if document is None else document.index_generation_ref,
                    processing_generation_ref=None if document is None else document.processing_generation_ref,
                    index_generation_ref=None if document is None else document.index_generation_ref,
                )
            )
        return tuple(snapshots)


class ProductionAuthorizedGrantResourceSource:
    """Build only the actor's current authorized exact document pins."""

    def __init__(self, rows: ProductionKnowledgeRowSource) -> None:
        self._rows = rows
    def current_scope(self, *, actor_id: str) -> frozenset[tuple[str, str]]:
        return self._rows.current_scope(actor_id=actor_id)


    def resources_for_grant(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        authorization_revision: int,
    ) -> list[GrantDocumentResourceV1]:
        snapshot = self._rows.grant_resources(
            actor_id=actor_id, conversation_id=conversation_id
        )
        if (
            not snapshot.authority.authorized
            or snapshot.authority.authorization_revision != authorization_revision
        ):
            raise PermissionError("grant resource authority changed before materialization")
        return self._resources_with_modalities(snapshot.documents)

    def resources_for_research(
        self, *, actor_id: str, project_ids: tuple[str, ...]
    ) -> list[GrantDocumentResourceV1]:
        documents = self._rows.authorized_documents_for_projects(
            actor_id=actor_id,
            project_ids=project_ids,
        )
        return self._resources_with_modalities(documents)

    def authorized_document_resources(self, *, actor_id: str) -> tuple[GrantDocumentResourceV1, ...]:
        """Narrow compatibility helper for adapter unit tests only."""
        return tuple(
            self._resources_with_modalities(
                self._rows.authorized_documents(actor_id=actor_id)
            )
        )
    def _resources_with_modalities(
        self, documents: tuple[CurrentDocumentResource, ...]
    ) -> list[GrantDocumentResourceV1]:
        modalities_by_document: dict[str, list[str]] = {
            document.document_id: [] for document in documents
        }
        for evidence in self._rows.evidence(documents=documents):
            values = modalities_by_document.get(evidence.document_id)
            if values is not None and evidence.modality not in values:
                values.append(evidence.modality)
        return [
            self._resource(
                document,
                modalities=modalities_by_document[document.document_id] or ["text"],
            )
            for document in documents
        ]

    @staticmethod
    def _resource(
        document: CurrentDocumentResource, *, modalities: list[str]
    ) -> GrantDocumentResourceV1:
        return GrantDocumentResourceV1(
            resource_ref=document.resource_ref,
            lifecycle_epoch=document.lifecycle_epoch,
            document_version_ref=document.document_version_ref,
            processing_generation_ref=document.processing_generation_ref,
            index_generation_ref=document.index_generation_ref,
            manifest_digest=document.manifest_digest,
            display_name=document.display_name,
            media_type=document.media_type,
            modalities=modalities,
            tags=[],
            language=None,
            created_at_label=document.uploaded_at,
            searchable_content=document.searchable_content,
            version_label=None,
        )
