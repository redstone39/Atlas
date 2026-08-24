from __future__ import annotations

from dataclasses import replace
from typing import Callable
from uuid import uuid4

from .api_models import (
    DocumentLibrarySummary,
    DocumentTagRef,
    DocumentTagSummary,
)
from atlas_production.shared.public import (
    AuditEventRecord,
    utc_now_iso,
)
from .records import (
    DocumentRecord,
    DocumentTagRecord,
    DocumentVersionRecord,
)
from atlas_production.modules.identity_access.records import (
    UserRecord,
)
from .contracts import DocumentAuditCommand
from .ports import DocumentIntakeRepository


class DocumentIntakeService:
    def __init__(
        self,
        repository: DocumentIntakeRepository,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self.repository = repository
        self.new_id = new_id

    def normalize_tag_refs(
        self,
        tag_refs: list[DocumentTagRef],
    ) -> list[DocumentTagRef]:
        refs = list(tag_refs)
        deduped: list[DocumentTagRef] = []
        seen: set[tuple[str, str]] = set()
        for ref in refs:
            key = (ref.tag_type, ref.tag_id)
            if key not in seen:
                seen.add(key)
                deduped.append(ref)
        return deduped

    def normalize_description(self, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()) or None

    def title_from_filename(self, filename: str | None) -> str | None:
        if not filename:
            return None
        name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not name:
            return None
        stem = name.rsplit(".", 1)[0].strip() if "." in name else name
        return " ".join((stem or name).split()) or None

    def generated_document_id(self) -> str:
        while True:
            document_id = f"doc-{self.new_id()}"
            if not self.repository.document_exists(document_id):
                return document_id

    def register_document(
        self,
        document: DocumentRecord,
        tag_refs: list[DocumentTagRef],
    ) -> DocumentVersionRecord:
        if not document.document_id:
            document = replace(document, document_id=self.generated_document_id())
        self.repository.replace_tags(document.document_id, tag_refs)
        self.repository.put_document(document)
        return self.repository.create_document_version(document)

    def update_document(self, document: DocumentRecord) -> None:
        self.repository.put_document(document)

    def get_document(self, document_id: str) -> DocumentRecord | None:
        return self.repository.get_document(document_id)

    def list_documents(self) -> list[DocumentRecord]:
        return self.repository.list_documents()

    def tags_for_document(self, document_id: str) -> list[DocumentTagRecord]:
        return self.repository.tags_for_document(document_id)

    def tag_summaries(self, document_id: str) -> list[DocumentTagSummary]:
        summaries = []
        for tag in sorted(
            self.repository.tags_for_document(document_id),
            key=lambda item: (item.tag_type, item.tag_id),
        ):
            summaries.append(
                DocumentTagSummary(
                    tag_type=tag.tag_type,
                    tag_id=tag.tag_id,
                    label=self.repository.scope_label(tag) or tag.tag_id,
                )
            )
        return summaries

    def active_or_create_version(self, document: DocumentRecord) -> str:
        version_id = self.repository.active_document_version_id(document.document_id)
        if version_id:
            return version_id
        return self.repository.create_document_version(document).document_version_id

    def processing_version_id(self, document: DocumentRecord) -> str:
        version_id = self.repository.processing_document_version_id(document.document_id)
        if version_id:
            return version_id
        return self.repository.create_document_version(document).document_version_id

    def evidence_count(self, document_id: str) -> int:
        return self.repository.count_ready_evidence(document_id)

    def document_summary(
        self,
        document: DocumentRecord,
        *,
        download_available: bool = False,
    ) -> DocumentLibrarySummary:
        assert document.scope_type in {"team", "project"}
        assert document.scope_id is not None
        return DocumentLibrarySummary(
            document_id=document.document_id,
            title=document.title,
            description=document.description,
            intake_status=document.intake_status,
            document_format=document.document_format,
            profile_id=document.processing_profile_id,
            profile_revision=document.processing_profile_revision,
            current_stage=document.current_stage,
            warning_codes=document.warning_codes,
            failure_code=document.failure_code,
            job_id=document.processing_job_id,
            lifecycle_status=document.lifecycle_status,
            uploader_actor_id=document.uploader_actor_id,
            scope_type=document.scope_type,
            scope_id=document.scope_id,
            direct_tags=self.tag_summaries(document.document_id),
            allow_member_download=document.allow_member_download,
            download_available=download_available,
            source_filename=document.source_filename,
            source_byte_size=document.source_byte_size,
            content_type=document.content_type,
            raw_sha256=document.raw_sha256,
            uploaded_at=document.uploaded_at,
            disabled_at=document.disabled_at,
            restored_at=document.restored_at,
            evidence_count=self.repository.count_ready_evidence(document.document_id),
        )

    def append_document_audit(
        self,
        event_type: str,
        actor: UserRecord,
        document: DocumentRecord,
        message_code: str,
        metadata: dict[str, object],
        *,
        persist: bool = True,
    ) -> AuditEventRecord:
        return self.repository.append_audit(
            DocumentAuditCommand(
                event_type=event_type,
                actor_id=actor.actor_id,
                target_ref=f"document:{document.document_id}",
                project_id=(
                    document.scope_id if document.scope_type == "project" else None
                ),
                scope_type=document.scope_type,
                scope_id=document.scope_id,
                document_id=document.document_id,
                message_code=message_code,
                metadata={"document_id": document.document_id, **metadata},
            ),
            persist=persist,
        )

    def document_audit_events(self, document_id: str) -> list[AuditEventRecord]:
        return self.repository.list_document_audit_events(document_id)

    def safe_header_filename(self, filename: str | None) -> str:
        candidate = (filename or "atlas-document").replace("\\", "/")
        candidate = candidate.rsplit("/", 1)[-1]
        candidate = "".join(
            character
            for character in candidate
            if ord(character) >= 32 and character != '"'
        ).strip()
        return candidate[:180].rstrip(". ") or "atlas-document"

    def is_library_document(self, document: DocumentRecord | None) -> bool:
        return bool(
            document
            and document.scope_type in {"team", "project"}
            and document.scope_id
        )

    def is_active_knowledge_document(self, document: DocumentRecord | None) -> bool:
        return bool(
            document
            and document.lifecycle_status == "active"
            and document.source_kind in {"file_upload", "inline_text"}
            and any(
                tag.tag_type in {"team", "project"}
                for tag in self.repository.tags_for_document(document.document_id)
            )
        )

    def authorized_direct_tags(
        self,
        document_id: str,
        authorized_scope: set[tuple[str, str]],
    ) -> list[DocumentTagRecord]:
        tags = [
            tag
            for tag in self.repository.tags_for_document(document_id)
            if (tag.tag_type, tag.tag_id) in authorized_scope
            and self.repository.scope_label(tag) is not None
        ]
        return sorted(
            tags,
            key=lambda tag: (
                tag.tag_type,
                (self.repository.scope_label(tag) or "").casefold(),
                tag.tag_id,
            ),
        )

    def scope_label(self, tag: DocumentTagRecord) -> str | None:
        return self.repository.scope_label(tag)
