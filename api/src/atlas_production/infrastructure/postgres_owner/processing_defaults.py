from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasPluginPackageRow,
    AtlasPluginVersionRow,
    AtlasProcessingProfileRevisionRow,
    AtlasProcessingProfileRow,
    AtlasRuntimeProfileRow,
    _processing_payload,
)
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.modules.processing_pipeline.records import (
    PluginPackageRecord,
    PluginVersionRecord,
    PluginVersionRef,
    ProcessingProfile,
    ProcessingProfileRevision,
    RuntimeProfileRecord,
)
from atlas_production.shared.public import AuditEventRecord, utc_now_iso


SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class ProcessingDefaultsSeedReceipt:
    created: bool
    runtime_profile_count: int
    plugin_count: int
    processing_profile_count: int


@dataclass(frozen=True, slots=True)
class _ProcessingDefaults:
    runtime_profiles: tuple[RuntimeProfileRecord, ...]
    packages: tuple[PluginPackageRecord, ...]
    plugin_versions: tuple[PluginVersionRecord, ...]
    profiles: tuple[ProcessingProfile, ...]
    revisions: tuple[ProcessingProfileRevision, ...]


_BUILTIN_SPECS = (
    ("atlas-pypdf", "atlas-python-v1", "base_parser", "PypdfPlugin", ("application/pdf",), (), (), (), ("generic_text",)),
    ("atlas-python-docx", "atlas-python-v1", "base_parser", "DocxPlugin", ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"), (), (), (), ("generic_text", "table", "image")),
    ("atlas-python-pptx", "atlas-python-v1", "base_parser", "PptxPlugin", ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.ms-powerpoint"), (), (), (), ("generic_text", "table", "image", "visual_semantics")),
    ("atlas-openpyxl", "atlas-python-v1", "base_parser", "XlsxPlugin", ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel"), (), (), (), ("generic_text", "table", "image")),
    ("atlas-libreoffice-doc", "atlas-python-v1", "base_parser", "DocxPlugin", ("application/msword",), (), (), (), ("generic_text", "table", "image")),
    ("atlas-libreoffice-ppt", "atlas-python-v1", "base_parser", "PptxPlugin", ("application/vnd.ms-powerpoint",), (), (), (), ("generic_text", "table", "image", "visual_semantics")),
    ("atlas-libreoffice-xls", "atlas-python-v1", "base_parser", "XlsxPlugin", ("application/vnd.ms-excel",), (), (), (), ("generic_text", "table", "image")),
    ("atlas-plain-text", "atlas-python-v1", "base_parser", "InlineTextPlugin", ("text/plain",), (), (), (), ("generic_text",)),
    ("atlas-csv", "atlas-python-v1", "base_parser", "CsvPlugin", ("text/csv",), (), (), (), ("generic_text", "table")),
    ("atlas-generic-text", "atlas-python-v1", "region_processor", "GenericTextPlugin", ("application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text/plain", "text/csv", "application/msword", "application/vnd.ms-powerpoint", "application/vnd.ms-excel"), ("page", "slide", "paragraph", "table", "figure", "image_region"), (), ("text", "table", "figure", "image", "unknown"), ("generic_text", "table")),
    ("atlas-rapidocr", "atlas-python-v1", "region_processor", "RapidOcrPlugin", ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/msword", "application/vnd.ms-powerpoint", "application/vnd.ms-excel"), ("image_region",), ("image",), ("image",), ("ocr_text",)),
    ("atlas-docling-layout", "atlas-docling-cpu-v1", "region_processor", "DoclingLayoutPlugin", ("application/pdf",), ("page",), ("page",), ("text", "unknown"), ("generic_text", "table")),
)


_PROFILE_SPECS = (
    ("default-pdf", "Default PDF", "application/pdf", "atlas-pypdf", ("atlas-docling-layout", "atlas-generic-text")),
    ("default-text", "Default text", "text/plain", "atlas-plain-text", ("atlas-generic-text",)),
    ("default-docx", "Default Word", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "atlas-python-docx", ("atlas-generic-text", "atlas-rapidocr")),
    ("default-pptx", "Default PowerPoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "atlas-python-pptx", ("atlas-generic-text", "atlas-rapidocr")),
    ("default-xlsx", "Default Excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "atlas-openpyxl", ("atlas-generic-text", "atlas-rapidocr")),
    ("default-csv", "Default CSV", "text/csv", "atlas-csv", ("atlas-generic-text",)),
    ("default-doc", "Default legacy Word", "application/msword", "atlas-libreoffice-doc", ("atlas-generic-text", "atlas-rapidocr")),
    ("default-ppt", "Default legacy PowerPoint", "application/vnd.ms-powerpoint", "atlas-libreoffice-ppt", ("atlas-generic-text", "atlas-rapidocr")),
    ("default-xls", "Default legacy Excel", "application/vnd.ms-excel", "atlas-libreoffice-xls", ("atlas-generic-text", "atlas-rapidocr")),
)


def _defaults(now: str) -> _ProcessingDefaults:
    runtime_profiles = (
        RuntimeProfileRecord("atlas-python-v1", "Atlas isolated Python runtime", True, now, {"pypdf": "6.0.0", "python-docx": "1.2.0", "python-pptx": "1.0.2", "openpyxl": "3.1.5", "rapidocr": "3.9.1", "onnxruntime": "1.27.0"}),
        RuntimeProfileRecord("atlas-docling-cpu-v1", "Atlas CPU Docling runtime", True, now, {"pypdf": "6.0.0", "docling": "2.111.0"}),
    )
    packages: list[PluginPackageRecord] = []
    plugin_versions: list[PluginVersionRecord] = []
    refs: dict[str, PluginVersionRef] = {}
    for plugin_id, runtime_profile, kind, plugin_class, media_types, region_kinds, element_hints, content_hints, channels in _BUILTIN_SPECS:
        digest = f"platform-builtin:{plugin_id}:1.0.0"
        packages.append(PluginPackageRecord(f"pkg-{plugin_id}-builtin", plugin_id, "1.0.0", digest, f"platform-builtin:{plugin_id}", 0, "atlas-platform", now))
        plugin_versions.append(
            PluginVersionRecord(
                plugin_id, "1.0.0", digest, runtime_profile, kind, "verified",
                "platform_builtin", 1, now, now,
                descriptor={
                    "entrypoint": f"atlas_plugin_runner.builtin_plugins:{plugin_class}",
                    "accepted_media_types": list(media_types),
                    "accepted_region_kinds": list(region_kinds),
                    "accepted_element_kind_hints": list(element_hints),
                    "accepted_content_kind_hints": list(content_hints),
                    "produced_channels": list(channels),
                    "output_contract_version": "eir-draft-v1",
                    "signature_key_id": "atlas-platform-builtin",
                    "license_expression": "Atlas-Internal",
                    "sdk_api_version": 1,
                    "sbom_present": True,
                    "sbom_spdx_version": "SPDX-2.3",
                    "checksums_verified": True,
                },
            )
        )
        refs[plugin_id] = PluginVersionRef(plugin_id, "1.0.0", digest, runtime_profile)

    profiles: list[ProcessingProfile] = []
    revisions: list[ProcessingProfileRevision] = []
    for profile_id, display_name, media_type, parser_id, processor_ids in _PROFILE_SPECS:
        processors = tuple(refs[plugin_id] for plugin_id in processor_ids)
        profiles.append(ProcessingProfile(profile_id, display_name, "atlas-platform", now))
        revisions.append(
            ProcessingProfileRevision(
                profile_id=profile_id,
                revision=1,
                status="active",
                accepted_media_types=(media_type,),
                base_parser_plugin_ref=refs[parser_id],
                mandatory_processor_plugin_refs=processors,
                eligible_processor_plugin_refs=processors,
                plugin_priority=processors,
                planner_enabled=False,
                planner_model_route_id=None,
                channel_registry_version="kpel-registry-v0.1",
                trait_registry_version="kpel-registry-v0.1",
                max_regions_per_plan=100,
                max_modules_per_region=4,
                max_total_plugin_invocations=500,
                planner_failure_behavior="mandatory_only",
                created_by="atlas-platform",
                created_at=now,
                activated_at=now,
            )
        )
    return _ProcessingDefaults(runtime_profiles, tuple(packages), tuple(plugin_versions), tuple(profiles), tuple(revisions))


def _registry_is_empty(session: Session) -> bool:
    return not any(
        session.scalar(select(row_type.id).limit(1)) is not None
        for row_type in (
            AtlasRuntimeProfileRow,
            AtlasPluginPackageRow,
            AtlasPluginVersionRow,
            AtlasProcessingProfileRow,
            AtlasProcessingProfileRevisionRow,
        )
    )


@dataclass(frozen=True, slots=True)
class SeedProcessingRegistryDefaultsCommand:
    session_factory: SessionFactory

    def execute(self) -> ProcessingDefaultsSeedReceipt:
        session = self.session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    domain_keys=("processing-registry:configuration-control",),
                )
                if not _registry_is_empty(session):
                    return ProcessingDefaultsSeedReceipt(
                        False,
                        2,
                        len(_BUILTIN_SPECS),
                        len(_PROFILE_SPECS),
                    )

                now = utc_now_iso()
                defaults = _defaults(now)
                session.add_all(
                    [AtlasRuntimeProfileRow(id=record.runtime_profile_id, payload=_processing_payload(record)) for record in defaults.runtime_profiles]
                    + [AtlasPluginPackageRow(id=record.package_id, payload=_processing_payload(record)) for record in defaults.packages]
                    + [AtlasPluginVersionRow(id=json.dumps([record.plugin_id, record.plugin_version], separators=(",", ":")), payload=_processing_payload(record)) for record in defaults.plugin_versions]
                    + [AtlasProcessingProfileRow(id=record.profile_id, payload=_processing_payload(record)) for record in defaults.profiles]
                    + [AtlasProcessingProfileRevisionRow(id=json.dumps([record.profile_id, record.revision], separators=(",", ":")), payload=_processing_payload(record)) for record in defaults.revisions]
                )
                AuditEventWriter(session).append(
                    AuditEventRecord(
                        event_id=f"audit-processing-defaults-{uuid4().hex}",
                        event_type="processing_registry.defaults_seeded",
                        actor_id=None,
                        target_ref="processing-registry:platform-defaults",
                        project_id=None,
                        message_code="processing.plugin_mutation_is_recorded",
                        message_params={},
                        metadata={"operation": "seed_platform_defaults"},
                        created_at=now,
                    )
                )
                session.commit()
                return ProcessingDefaultsSeedReceipt(
                    True,
                    len(defaults.runtime_profiles),
                    len(defaults.plugin_versions),
                    len(defaults.profiles),
                )
            except Exception:
                session.rollback()
                raise


__all__ = [
    "ProcessingDefaultsSeedReceipt",
    "SeedProcessingRegistryDefaultsCommand",
]
