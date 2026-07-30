from __future__ import annotations

from typing import Protocol

from .api_models import (
    DocumentTagRef,
)
from atlas_production.shared.public import (
    AuditEventRecord,
)
from .records import (
    DocumentRecord,
    DocumentTagRecord,
    DocumentVersionRecord,
)
from .contracts import DocumentAuditCommand


class DocumentIntakeRepository(Protocol):
    def get_document(self, document_id: str) -> DocumentRecord | None: ...

    def list_documents(self) -> list[DocumentRecord]: ...

    def put_document(self, document: DocumentRecord) -> None: ...

    def document_exists(self, document_id: str) -> bool: ...

    def replace_tags(self, document_id: str, tag_refs: list[DocumentTagRef]) -> None: ...

    def tags_for_document(self, document_id: str) -> list[DocumentTagRecord]: ...

    def scope_label(self, tag: DocumentTagRecord) -> str | None: ...

    def active_document_version_id(self, document_id: str) -> str | None: ...

    def processing_document_version_id(self, document_id: str) -> str | None: ...

    def create_document_version(self, document: DocumentRecord) -> DocumentVersionRecord: ...

    def count_ready_evidence(self, document_id: str) -> int: ...

    def append_audit(
        self, command: DocumentAuditCommand, *, persist: bool = True
    ) -> AuditEventRecord: ...

    def list_document_audit_events(self, document_id: str) -> list[AuditEventRecord]: ...
