"""Stable public facade for production ACL/currentness and knowledge-read adapters.

Implementation responsibilities are split into feature-private modules. Existing imports
continue to resolve to the same class and contract objects.
"""

from atlas_production.infrastructure.postgres_turn_knowledge_authorization import (
    ProductionAuthorizedGrantResourceSource,
    ProductionCurrentResourceAuthorizationReader,
)
from atlas_production.infrastructure.postgres_turn_knowledge_backend import (
    ProductionKnowledgeRetrievalBackend,
)
from atlas_production.infrastructure.postgres_turn_knowledge_contracts import (
    CurrentDiscoveryMatch,
    CurrentDocumentResource,
    CurrentEvidenceResource,
    CurrentResourceState,
    GrantAuthorityState,
    GrantResourceSnapshot,
    ProductionKnowledgeRowSource,
    SessionFactory,
    _apply_statement_deadline,
    _digest,
    _opaque_evidence_ref,
    _parse_visual_citation_ref,
    _remaining_seconds,
    canonical_document_resource_ref,
)
from atlas_production.infrastructure.postgres_turn_knowledge_rows import (
    PostgresProductionKnowledgeRowSource,
)
from atlas_production.infrastructure.postgres_turn_knowledge_visual import (
    PostgresVisualPageRenderer,
)


__all__ = [
    "PostgresProductionKnowledgeRowSource",
    "PostgresVisualPageRenderer",
    "ProductionAuthorizedGrantResourceSource",
    "ProductionCurrentResourceAuthorizationReader",
    "ProductionKnowledgeRetrievalBackend",
    "canonical_document_resource_ref",
]
