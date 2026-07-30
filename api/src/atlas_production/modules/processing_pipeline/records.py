from dataclasses import dataclass, field
from typing import Any, Literal


EvidenceStatus = Literal["staged", "ready", "superseded", "blocked"]
_EVIDENCE_STATUS_TRANSITIONS: dict[EvidenceStatus, frozenset[EvidenceStatus]] = {
    "staged": frozenset({"ready"}),
    "ready": frozenset({"superseded", "blocked"}),
    "blocked": frozenset({"ready", "superseded"}),
    "superseded": frozenset(),
}


@dataclass
class EvidenceRecord:
    evidence_id: str
    document_id: str
    document_title: str
    locator_label: str
    snippet: str
    content: str
    document_version_id: str
    processing_generation: int | None = None
    status: EvidenceStatus = "ready"
    source_region_id: str | None = None
    channel_id: str = "generic_text"
    output_contract_version: str = "eir-draft-v1"
    claim_support_role: str = "claim_grounding"
    locator_payload: dict[str, Any] = field(default_factory=dict)
    content_fingerprint: str = ""
    processing_fingerprint: str = ""
    profile_id: str = ""
    profile_revision: int = 0
    promotion_decision_id: str | None = None
    quality_flag_refs: list[str] = field(default_factory=list)
    trace_ref: str | None = None
    supersedes_evidence_id: str | None = None
    evidence_artifact_id: str | None = None

    def transition_to(self, status: EvidenceStatus) -> None:
        if status == self.status:
            return
        if status not in _EVIDENCE_STATUS_TRANSITIONS[self.status]:
            raise ValueError(f"invalid evidence status transition: {self.status} -> {status}")
        self.status = status


@dataclass
class EvidencePageArtifact:
    artifact_id: str
    tenant_id: str
    document_version_id: str
    source_page_index: int
    source_page_label: str
    artifact_kind: Literal["pdf_single_page", "page_image"]
    artifact_digest: str
    content_length: int
    storage_artifact_id: str
    source_crop_box: list[float]
    source_rotation: Literal[0, 90, 180, 270]
    geometry_transform_version: str
    renderer_version: str
    created_at: str
    processing_generation: int = 0
    width: int | None = None
    height: int | None = None
    render_config_revision: str | None = None
    quality_flag_refs: list[str] = field(default_factory=list)


@dataclass
class TextEvidenceProjection:
    projection_id: str
    tenant_id: str
    document_version_id: str
    block_id: str
    content_length: int
    block_text_digest: str
    normalization_version: str
    content: str


PluginLifecycle = Literal["uploaded", "validating", "quarantined", "verified", "disabled", "rejected"]


@dataclass(frozen=True)
class PluginVersionRef:
    plugin_id: str
    plugin_version: str
    package_digest: str
    runtime_profile: str


@dataclass
class PluginPackageRecord:
    package_id: str
    plugin_id: str
    plugin_version: str
    package_digest: str
    artifact_ref: str
    byte_size: int
    uploaded_by: str
    created_at: str


@dataclass
class PluginVersionRecord:
    plugin_id: str
    plugin_version: str
    package_digest: str
    runtime_profile: str
    plugin_kind: str
    status: PluginLifecycle
    trust_provenance: str
    revision: int
    created_at: str
    updated_at: str
    diagnostic_code: str | None = None
    canary_passed_at: str | None = None
    descriptor: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeProfileRecord:
    runtime_profile_id: str
    description: str
    enabled: bool
    created_at: str
    available_packages: dict[str, str] = field(default_factory=dict)


@dataclass
class ProcessingProfile:
    profile_id: str
    display_name: str
    created_by: str
    created_at: str


@dataclass(frozen=True)
class ProcessingProfileRevision:
    profile_id: str
    revision: int
    status: Literal["draft", "canary", "active", "deprecated"]
    accepted_media_types: tuple[str, ...]
    base_parser_plugin_ref: PluginVersionRef
    mandatory_processor_plugin_refs: tuple[PluginVersionRef, ...]
    eligible_processor_plugin_refs: tuple[PluginVersionRef, ...]
    plugin_priority: tuple[PluginVersionRef, ...]
    planner_enabled: bool
    planner_model_route_id: str | None
    channel_registry_version: str
    trait_registry_version: str
    max_regions_per_plan: int
    max_modules_per_region: int
    max_total_plugin_invocations: int
    planner_failure_behavior: str
    created_by: str
    created_at: str
    activated_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "accepted_media_types", tuple(self.accepted_media_types))
        object.__setattr__(self, "mandatory_processor_plugin_refs", tuple(self.mandatory_processor_plugin_refs))
        object.__setattr__(self, "eligible_processor_plugin_refs", tuple(self.eligible_processor_plugin_refs))
        object.__setattr__(self, "plugin_priority", tuple(self.plugin_priority))


@dataclass
class ProcessingRun:
    run_id: str
    document_id: str
    document_version_id: str
    profile_id: str
    profile_revision: int
    status: str
    attempt: int
    created_by: str
    created_at: str
    updated_at: str
    media_type: str = "application/octet-stream"
    base_parser_plugin_ref: PluginVersionRef | None = None
    mandatory_processor_plugin_refs: tuple[PluginVersionRef, ...] = field(default_factory=tuple)
    eligible_processor_plugin_refs: tuple[PluginVersionRef, ...] = field(default_factory=tuple)
    plugin_priority: tuple[PluginVersionRef, ...] = field(default_factory=tuple)
    channel_registry_version: str = ""
    trait_registry_version: str = ""
    policy_snapshot_ref: str = ""
    policy_snapshot_digest: str = ""
    policy_snapshot_payload: dict[str, Any] = field(default_factory=dict)
    warning_codes: list[str] = field(default_factory=list)
    failure_code: str | None = None


@dataclass
class ParserAdapterInvocation:
    invocation_id: str
    run_id: str
    plugin_ref: PluginVersionRef
    status: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceRegion:
    region_id: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionCandidate:
    candidate_id: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateGroup:
    group_id: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PromotionDecision:
    decision_id: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class KPELNormalizationHandoff:
    handoff_id: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingDecision:
    routing_decision_id: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceBuildTrace:
    trace_id: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessingIdempotencyRecord:
    idempotency_key: str
    operation: str
    request_digest: str
    response_payload: dict[str, Any]
    status_code: int
    created_at: str
