from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable, cast

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence import processing_pipeline
from atlas_production.infrastructure.persistence.processing_pipeline import (
    AtlasPluginPackageRow,
    AtlasPluginVersionRow,
    AtlasParserAdapterInvocationRow,
    AtlasSourceRegionRow,
    AtlasExtractionCandidateRow,
    AtlasCandidateGroupRow,
    AtlasPromotionDecisionRow,
    AtlasKpelHandoffRow,
    AtlasProcessingRoutingDecisionRow,
    AtlasEvidenceBuildTraceRow,
    AtlasProcessingIdempotencyRow,
    AtlasProcessingProfileRevisionRow,
    AtlasProcessingProfileRow,
    AtlasProcessingRunRow,
    AtlasRuntimeProfileRow,
)
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.modules.processing_pipeline.records import (
    PluginPackageRecord,
    PluginVersionRecord,
    ParserAdapterInvocation,
    SourceRegion,
    ExtractionCandidate,
    CandidateGroup,
    PromotionDecision,
    KPELNormalizationHandoff,
    RoutingDecision,
    EvidenceBuildTrace,
    ProcessingIdempotencyRecord,
    ProcessingProfile,
    ProcessingProfileRevision,
    ProcessingRun,
    RuntimeProfileRecord,
)
from atlas_production.shared.public import AuditEventRecord


SessionFactory = Callable[[], Session]
_MAX_READ_LIMIT = 500


def _compound_id(*parts: str | int) -> str:
    return json.dumps(list(parts), separators=(",", ":"))


def _payload(record: object) -> dict[str, object]:
    return cast(
        dict[str, object],
        processing_pipeline._processing_payload(record),
    )


def _record(row: object, record_type: type[object]) -> object:
    return processing_pipeline._record_from_payload(
        record_type,
        cast(dict[str, object], getattr(row, "payload")),
    )


def _bounded_limit(limit: int, *, family: str) -> int:
    if limit < 1 or limit > _MAX_READ_LIMIT:
        raise ValueError(f"{family} limit must be between 1 and 500")
    return limit


@dataclass(frozen=True, slots=True)
class PluginVersionWrite:
    record: PluginVersionRecord
    expected_revision: int | None

    def __post_init__(self) -> None:
        if self.expected_revision is None:
            if self.record.revision != 1:
                raise ValueError("new plugin version revision must start at 1")
            return
        if self.expected_revision < 1 or self.record.revision != self.expected_revision + 1:
            raise ValueError("plugin version write must advance the expected revision")


@dataclass(frozen=True, slots=True)
class ProcessingProfileRevisionWrite:
    record: ProcessingProfileRevision
    expected_status: str | None


@dataclass(frozen=True, slots=True)
class PluginDisablePrecondition:
    plugin_id: str
    plugin_version: str


@dataclass(frozen=True, slots=True)
class PluginActivationDependency:
    plugin_id: str
    plugin_version: str
    expected_revision: int
    expected_status: str
    expected_trust_provenance: str
    expected_canary_passed_at: str | None


@dataclass(frozen=True, slots=True)
class ProfileActivationPrecondition:
    profile_id: str
    revision: int
    accepted_media_types: tuple[str, ...]
    plugin_dependencies: tuple[PluginActivationDependency, ...]


@dataclass(frozen=True, slots=True)
class ProcessingRunWrite:
    record: ProcessingRun
    expected_status: str | None = None
    expected_attempt: int | None = None

    def __post_init__(self) -> None:
        if (self.expected_status is None) != (self.expected_attempt is None):
            raise ValueError(
                "processing run currentness requires status and attempt together"
            )
        if self.expected_attempt is not None and self.expected_attempt < 1:
            raise ValueError("processing run expected attempt must be positive")


@dataclass(frozen=True, slots=True)
class ParserInvocationWrite:
    record: ParserAdapterInvocation
    expected: ParserAdapterInvocation | None = None


@dataclass(frozen=True, slots=True)
class SourceRegionWrite:
    record: SourceRegion
    expected: SourceRegion | None = None


@dataclass(frozen=True, slots=True)
class ExtractionCandidateWrite:
    record: ExtractionCandidate
    expected: ExtractionCandidate | None = None


@dataclass(frozen=True, slots=True)
class CandidateGroupWrite:
    record: CandidateGroup
    expected: CandidateGroup | None = None


@dataclass(frozen=True, slots=True)
class PromotionDecisionWrite:
    record: PromotionDecision
    expected: PromotionDecision | None = None


@dataclass(frozen=True, slots=True)
class KpelHandoffWrite:
    record: KPELNormalizationHandoff
    expected: KPELNormalizationHandoff | None = None


@dataclass(frozen=True, slots=True)
class RoutingDecisionWrite:
    record: RoutingDecision
    expected: RoutingDecision | None = None


@dataclass(frozen=True, slots=True)
class EvidenceTraceWrite:
    record: EvidenceBuildTrace
    expected: EvidenceBuildTrace | None = None


@dataclass(frozen=True, slots=True)
class _ProcessingRegistryWriteBatch:
    packages: tuple[PluginPackageRecord, ...] = ()
    plugin_versions: tuple[PluginVersionWrite, ...] = ()
    runtime_profiles: tuple[RuntimeProfileRecord, ...] = ()
    processing_profiles: tuple[ProcessingProfile, ...] = ()
    profile_revisions: tuple[ProcessingProfileRevisionWrite, ...] = ()
    runs: tuple[ProcessingRunWrite, ...] = ()
    parser_invocations: tuple[ParserInvocationWrite, ...] = ()
    source_regions: tuple[SourceRegionWrite, ...] = ()
    extraction_candidates: tuple[ExtractionCandidateWrite, ...] = ()
    candidate_groups: tuple[CandidateGroupWrite, ...] = ()
    promotion_decisions: tuple[PromotionDecisionWrite, ...] = ()
    kpel_handoffs: tuple[KpelHandoffWrite, ...] = ()
    routing_decisions: tuple[RoutingDecisionWrite, ...] = ()
    evidence_traces: tuple[EvidenceTraceWrite, ...] = ()
    idempotency_records: tuple[ProcessingIdempotencyRecord, ...] = ()
    audit_events: tuple[AuditEventRecord, ...] = ()
    plugin_disable_precondition: PluginDisablePrecondition | None = None
    profile_activation_precondition: ProfileActivationPrecondition | None = None

    def __post_init__(self) -> None:
        mutations = (
            self.packages,
            self.plugin_versions,
            self.runtime_profiles,
            self.processing_profiles,
            self.profile_revisions,
            self.runs,
            self.parser_invocations,
            self.source_regions,
            self.extraction_candidates,
            self.candidate_groups,
            self.promotion_decisions,
            self.kpel_handoffs,
            self.routing_decisions,
            self.evidence_traces,
            self.idempotency_records,
        )
        if any(mutations) and not self.audit_events:
            raise ValueError("processing-registry mutation requires audit events")
        identities = (
            *(f"package:{record.package_id}" for record in self.packages),
            *(
                f"plugin-version:{write.record.plugin_id}:{write.record.plugin_version}"
                for write in self.plugin_versions
            ),
            *(
                f"runtime-profile:{record.runtime_profile_id}"
                for record in self.runtime_profiles
            ),
            *(
                f"processing-profile:{record.profile_id}"
                for record in self.processing_profiles
            ),
            *(
                f"profile-revision:{write.record.profile_id}:{write.record.revision}"
                for write in self.profile_revisions
            ),
            *(f"run:{write.record.run_id}" for write in self.runs),
            *(f"parser-invocation:{write.record.invocation_id}" for write in self.parser_invocations),
            *(f"source-region:{write.record.region_id}" for write in self.source_regions),
            *(f"extraction-candidate:{write.record.candidate_id}" for write in self.extraction_candidates),
            *(f"candidate-group:{write.record.group_id}" for write in self.candidate_groups),
            *(f"promotion-decision:{write.record.decision_id}" for write in self.promotion_decisions),
            *(f"kpel-handoff:{write.record.handoff_id}" for write in self.kpel_handoffs),
            *(f"routing-decision:{write.record.routing_decision_id}" for write in self.routing_decisions),
            *(f"evidence-trace:{write.record.trace_id}" for write in self.evidence_traces),
            *(
                f"idempotency:{record.idempotency_key}"
                for record in self.idempotency_records
            ),
        )
        if len(identities) != len(set(identities)):
            raise ValueError("processing-registry change set contains duplicate owners")


class ProcessingRegistryCurrentnessConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _ProcessingCommandCoordinator:
    session_factory: SessionFactory

    def _finalize(self, change_set: _ProcessingRegistryWriteBatch) -> None:
        session = self.session_factory()
        with session:
            try:
                configuration_change = any(
                    (
                        change_set.packages,
                        change_set.plugin_versions,
                        change_set.runtime_profiles,
                        change_set.processing_profiles,
                        change_set.profile_revisions,
                    )
                )
                acquire_owner_locks(
                    session,
                    domain_keys=("processing-registry:configuration-control",)
                    if configuration_change
                    else (),
                    identity_keys=self._identity_keys(change_set),
                )
                self._validate_rule_preconditions(session, change_set)
                self._lock_current_rows(session, change_set)
                self._write_rows(session, change_set)
                AuditEventWriter(session).append_many(change_set.audit_events)
                session.commit()
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _active_profile_reference(
        session: Session, dependency: PluginDisablePrecondition
    ) -> object | None:
        exact_ref = {
            "plugin_id": dependency.plugin_id,
            "plugin_version": dependency.plugin_version,
        }
        return session.scalar(
            select(AtlasProcessingProfileRevisionRow)
            .where(
                AtlasProcessingProfileRevisionRow.payload["status"].as_string()
                == "active",
                or_(
                    AtlasProcessingProfileRevisionRow.payload[
                        "base_parser_plugin_ref"
                    ].contains(exact_ref),
                    AtlasProcessingProfileRevisionRow.payload[
                        "eligible_processor_plugin_refs"
                    ].contains([exact_ref]),
                ),
            )
            .order_by(AtlasProcessingProfileRevisionRow.id)
            .with_for_update()
        )

    @staticmethod
    def _overlapping_active_profile(
        session: Session, activation: ProfileActivationPrecondition
    ) -> object | None:
        media_predicates = tuple(
            AtlasProcessingProfileRevisionRow.payload[
                "accepted_media_types"
            ].contains([media_type])
            for media_type in activation.accepted_media_types
        )
        if not media_predicates:
            return None
        return session.scalar(
            select(AtlasProcessingProfileRevisionRow)
            .where(
                AtlasProcessingProfileRevisionRow.payload["status"].as_string()
                == "active",
                AtlasProcessingProfileRevisionRow.payload["profile_id"].as_string()
                != activation.profile_id,
                or_(*media_predicates),
            )
            .order_by(AtlasProcessingProfileRevisionRow.id)
            .with_for_update()
        )

    @staticmethod
    def _validate_rule_preconditions(
        session: Session, change_set: _ProcessingRegistryWriteBatch
    ) -> None:
        disable = change_set.plugin_disable_precondition
        activation = change_set.profile_activation_precondition
        if disable is not None and (
            _ProcessingCommandCoordinator._active_profile_reference(
                session, disable
            )
            is not None
        ):
            raise ProcessingRegistryCurrentnessConflict(
                "plugin became referenced by an active processing profile"
            )
        if activation is None:
            return
        if (
            _ProcessingCommandCoordinator._overlapping_active_profile(
                session, activation
            )
            is not None
        ):
            raise ProcessingRegistryCurrentnessConflict(
                "processing profile active MIME predicate changed"
            )
        for dependency in activation.plugin_dependencies:
            current = session.scalar(
                select(AtlasPluginVersionRow)
                .where(
                    AtlasPluginVersionRow.id
                    == _compound_id(
                        dependency.plugin_id, dependency.plugin_version
                    )
                )
                .with_for_update()
            )
            payload = current.payload if current is not None else {}
            if (
                current is None
                or payload.get("revision") != dependency.expected_revision
                or payload.get("status") != dependency.expected_status
                or payload.get("trust_provenance")
                != dependency.expected_trust_provenance
                or payload.get("canary_passed_at")
                != dependency.expected_canary_passed_at
                or payload.get("status") != "verified"
                or (
                    payload.get("trust_provenance") != "platform_builtin"
                    and not payload.get("canary_passed_at")
                )
            ):
                raise ProcessingRegistryCurrentnessConflict(
                    "processing profile plugin dependency changed"
                )

    @staticmethod
    def _identity_keys(change_set: _ProcessingRegistryWriteBatch) -> tuple[str, ...]:
        return (
            *(
                f"processing-registry:package:{record.package_id}"
                for record in change_set.packages
            ),
            *(
                "processing-registry:plugin-version:"
                f"{write.record.plugin_id}:{write.record.plugin_version}"
                for write in change_set.plugin_versions
            ),
            *(
                f"processing-registry:runtime-profile:{record.runtime_profile_id}"
                for record in change_set.runtime_profiles
            ),
            *(
                f"processing-registry:profile:{record.profile_id}"
                for record in change_set.processing_profiles
            ),
            *(
                "processing-registry:profile-revision:"
                f"{write.record.profile_id}:{write.record.revision}"
                for write in change_set.profile_revisions
            ),
            *(
                f"processing-registry:run:{write.record.run_id}"
                for write in change_set.runs
            ),
            *(f"processing-registry:parser-invocation:{write.record.invocation_id}" for write in change_set.parser_invocations),
            *(f"processing-registry:source-region:{write.record.region_id}" for write in change_set.source_regions),
            *(f"processing-registry:extraction-candidate:{write.record.candidate_id}" for write in change_set.extraction_candidates),
            *(f"processing-registry:candidate-group:{write.record.group_id}" for write in change_set.candidate_groups),
            *(f"processing-registry:promotion-decision:{write.record.decision_id}" for write in change_set.promotion_decisions),
            *(f"processing-registry:kpel-handoff:{write.record.handoff_id}" for write in change_set.kpel_handoffs),
            *(f"processing-registry:routing-decision:{write.record.routing_decision_id}" for write in change_set.routing_decisions),
            *(f"processing-registry:evidence-trace:{write.record.trace_id}" for write in change_set.evidence_traces),
            *(
                f"processing-registry:idempotency:{record.idempotency_key}"
                for record in change_set.idempotency_records
            ),
        )

    @staticmethod
    def _lock_current_rows(
        session: Session,
        change_set: _ProcessingRegistryWriteBatch,
    ) -> None:
        for write in change_set.plugin_versions:
            row_id = _compound_id(
                write.record.plugin_id,
                write.record.plugin_version,
            )
            current = session.scalar(
                select(AtlasPluginVersionRow)
                .where(AtlasPluginVersionRow.id == row_id)
                .with_for_update()
            )
            if write.expected_revision is None:
                if current is not None:
                    raise ProcessingRegistryCurrentnessConflict(
                        "plugin version already exists"
                    )
            elif (
                current is None
                or current.payload.get("revision") != write.expected_revision
            ):
                raise ProcessingRegistryCurrentnessConflict(
                    "plugin version revision changed"
                )

        for write in change_set.profile_revisions:
            row_id = _compound_id(write.record.profile_id, write.record.revision)
            current = session.scalar(
                select(AtlasProcessingProfileRevisionRow)
                .where(AtlasProcessingProfileRevisionRow.id == row_id)
                .with_for_update()
            )
            if write.expected_status is None:
                if current is not None:
                    raise ProcessingRegistryCurrentnessConflict(
                        "processing profile revision already exists"
                    )
            elif (
                current is None
                or current.payload.get("status") != write.expected_status
            ):
                raise ProcessingRegistryCurrentnessConflict(
                    "processing profile revision status changed"
                )

        for write in change_set.runs:
            current = session.scalar(
                select(AtlasProcessingRunRow)
                .where(AtlasProcessingRunRow.id == write.record.run_id)
                .with_for_update()
            )
            if write.expected_status is None:
                if current is not None:
                    raise ProcessingRegistryCurrentnessConflict(
                        "processing run already exists"
                    )
            elif (
                current is None
                or current.payload.get("status") != write.expected_status
                or current.payload.get("attempt") != write.expected_attempt
            ):
                raise ProcessingRegistryCurrentnessConflict(
                    "processing run fence changed"
                )

        target_run_ids = {write.record.run_id for write in change_set.runs}
        for child in (
            *(write.record for write in change_set.parser_invocations),
            *(write.record for write in change_set.source_regions),
            *(write.record for write in change_set.extraction_candidates),
            *(write.record for write in change_set.candidate_groups),
            *(write.record for write in change_set.promotion_decisions),
            *(write.record for write in change_set.kpel_handoffs),
            *(write.record for write in change_set.routing_decisions),
            *(write.record for write in change_set.evidence_traces),
        ):
            if child.run_id not in target_run_ids:
                raise ProcessingRegistryCurrentnessConflict(
                    "processing child belongs to a foreign run"
                )

        _ProcessingCommandCoordinator._lock_child_preimages(
            session, AtlasParserAdapterInvocationRow, change_set.parser_invocations,
            lambda write: write.record.invocation_id, ParserAdapterInvocation,
        )
        _ProcessingCommandCoordinator._lock_child_preimages(
            session, AtlasSourceRegionRow, change_set.source_regions,
            lambda write: write.record.region_id, SourceRegion,
        )
        _ProcessingCommandCoordinator._lock_child_preimages(
            session, AtlasExtractionCandidateRow, change_set.extraction_candidates,
            lambda write: write.record.candidate_id, ExtractionCandidate,
        )
        _ProcessingCommandCoordinator._lock_child_preimages(
            session, AtlasCandidateGroupRow, change_set.candidate_groups,
            lambda write: write.record.group_id, CandidateGroup,
        )
        _ProcessingCommandCoordinator._lock_child_preimages(
            session, AtlasPromotionDecisionRow, change_set.promotion_decisions,
            lambda write: write.record.decision_id, PromotionDecision,
        )
        _ProcessingCommandCoordinator._lock_child_preimages(
            session, AtlasKpelHandoffRow, change_set.kpel_handoffs,
            lambda write: write.record.handoff_id, KPELNormalizationHandoff,
        )
        _ProcessingCommandCoordinator._lock_child_preimages(
            session, AtlasProcessingRoutingDecisionRow, change_set.routing_decisions,
            lambda write: write.record.routing_decision_id, RoutingDecision,
        )
        _ProcessingCommandCoordinator._lock_child_preimages(
            session, AtlasEvidenceBuildTraceRow, change_set.evidence_traces,
            lambda write: write.record.trace_id, EvidenceBuildTrace,
        )

        for replay in change_set.idempotency_records:
            current = session.scalar(
                select(AtlasProcessingIdempotencyRow)
                .where(AtlasProcessingIdempotencyRow.id == replay.idempotency_key)
                .with_for_update()
            )
            if current is not None:
                raise ProcessingRegistryCurrentnessConflict(
                    "processing idempotency key already exists"
                )

    @staticmethod
    def _lock_child_preimages(
        session: Session,
        row_type: type,
        writes: tuple,
        identity: Callable[[object], str],
        record_type: type,
    ) -> None:
        for write in writes:
            current = session.scalar(
                select(row_type).where(row_type.id == identity(write)).with_for_update()
            )
            current_record = (
                _record(current, record_type) if current is not None else None
            )
            if current_record != write.expected:
                raise ProcessingRegistryCurrentnessConflict(
                    f"{record_type.__name__} preimage changed"
                )

    @staticmethod
    def _write_rows(
        session: Session,
        change_set: _ProcessingRegistryWriteBatch,
    ) -> None:
        for record in change_set.packages:
            session.add(
                AtlasPluginPackageRow(id=record.package_id, payload=_payload(record))
            )
        for write in change_set.plugin_versions:
            session.merge(
                AtlasPluginVersionRow(
                    id=_compound_id(
                        write.record.plugin_id,
                        write.record.plugin_version,
                    ),
                    payload=_payload(write.record),
                )
            )
        for record in change_set.runtime_profiles:
            session.merge(
                AtlasRuntimeProfileRow(
                    id=record.runtime_profile_id,
                    payload=_payload(record),
                )
            )
        for record in change_set.processing_profiles:
            session.add(
                AtlasProcessingProfileRow(
                    id=record.profile_id,
                    payload=_payload(record),
                )
            )
        for write in change_set.profile_revisions:
            session.merge(
                AtlasProcessingProfileRevisionRow(
                    id=_compound_id(
                        write.record.profile_id,
                        write.record.revision,
                    ),
                    payload=_payload(write.record),
                )
            )
        for write in change_set.runs:
            session.merge(
                AtlasProcessingRunRow(
                    id=write.record.run_id,
                    payload=_payload(write.record),
                )
            )
        _ProcessingCommandCoordinator._write_children(session, AtlasParserAdapterInvocationRow, change_set.parser_invocations, lambda write: write.record.invocation_id)
        _ProcessingCommandCoordinator._write_children(session, AtlasSourceRegionRow, change_set.source_regions, lambda write: write.record.region_id)
        _ProcessingCommandCoordinator._write_children(session, AtlasExtractionCandidateRow, change_set.extraction_candidates, lambda write: write.record.candidate_id)
        _ProcessingCommandCoordinator._write_children(session, AtlasCandidateGroupRow, change_set.candidate_groups, lambda write: write.record.group_id)
        _ProcessingCommandCoordinator._write_children(session, AtlasPromotionDecisionRow, change_set.promotion_decisions, lambda write: write.record.decision_id)
        _ProcessingCommandCoordinator._write_children(session, AtlasKpelHandoffRow, change_set.kpel_handoffs, lambda write: write.record.handoff_id)
        _ProcessingCommandCoordinator._write_children(session, AtlasProcessingRoutingDecisionRow, change_set.routing_decisions, lambda write: write.record.routing_decision_id)
        _ProcessingCommandCoordinator._write_children(session, AtlasEvidenceBuildTraceRow, change_set.evidence_traces, lambda write: write.record.trace_id)
        for record in change_set.idempotency_records:
            session.add(
                AtlasProcessingIdempotencyRow(
                    id=record.idempotency_key,
                    payload=_payload(record),
                )
            )

    @staticmethod
    def _write_children(
        session: Session,
        row_type: type,
        writes: tuple,
        identity: Callable[[object], str],
    ) -> None:
        for write in writes:
            session.merge(
                row_type(id=identity(write), payload=_payload(write.record))
            )

@dataclass(frozen=True, slots=True)
class ProcessingRegistryReadModel:
    session_factory: SessionFactory

    def get_package(self, package_id: str) -> PluginPackageRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasPluginPackageRow).where(
                    AtlasPluginPackageRow.id == package_id
                )
            )
            return (
                cast(PluginPackageRecord, _record(row, PluginPackageRecord))
                if row is not None
                else None
            )

    def list_packages(self, *, limit: int = 200) -> list[PluginPackageRecord]:
        _bounded_limit(limit, family="plugin package")
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasPluginPackageRow)
                .order_by(AtlasPluginPackageRow.id)
                .limit(limit)
            ).all()
            return [
                cast(PluginPackageRecord, _record(row, PluginPackageRecord))
                for row in rows
            ]

    def get_plugin_version(
        self,
        plugin_id: str,
        plugin_version: str,
    ) -> PluginVersionRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasPluginVersionRow).where(
                    AtlasPluginVersionRow.id
                    == _compound_id(plugin_id, plugin_version)
                )
            )
            return (
                cast(PluginVersionRecord, _record(row, PluginVersionRecord))
                if row is not None
                else None
            )

    def list_plugin_versions(self, *, limit: int = 200) -> list[PluginVersionRecord]:
        _bounded_limit(limit, family="plugin version")
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasPluginVersionRow)
                .order_by(AtlasPluginVersionRow.id)
                .limit(limit)
            ).all()
            return [
                cast(PluginVersionRecord, _record(row, PluginVersionRecord))
                for row in rows
            ]

    def get_runtime_profile(
        self,
        runtime_profile_id: str,
    ) -> RuntimeProfileRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasRuntimeProfileRow).where(
                    AtlasRuntimeProfileRow.id == runtime_profile_id
                )
            )
            return (
                cast(RuntimeProfileRecord, _record(row, RuntimeProfileRecord))
                if row is not None
                else None
            )

    def list_runtime_profiles(
        self,
        *,
        limit: int = 200,
    ) -> list[RuntimeProfileRecord]:
        _bounded_limit(limit, family="runtime profile")
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasRuntimeProfileRow)
                .order_by(AtlasRuntimeProfileRow.id)
                .limit(limit)
            ).all()
            return [
                cast(RuntimeProfileRecord, _record(row, RuntimeProfileRecord))
                for row in rows
            ]

    def get_processing_profile(self, profile_id: str) -> ProcessingProfile | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasProcessingProfileRow).where(
                    AtlasProcessingProfileRow.id == profile_id
                )
            )
            return (
                cast(ProcessingProfile, _record(row, ProcessingProfile))
                if row is not None
                else None
            )

    def list_processing_profiles(
        self,
        *,
        limit: int = 200,
    ) -> list[ProcessingProfile]:
        _bounded_limit(limit, family="processing profile")
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasProcessingProfileRow)
                .order_by(AtlasProcessingProfileRow.id)
                .limit(limit)
            ).all()
            return [
                cast(ProcessingProfile, _record(row, ProcessingProfile))
                for row in rows
            ]

    def get_profile_revision(
        self,
        profile_id: str,
        revision: int,
    ) -> ProcessingProfileRevision | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasProcessingProfileRevisionRow).where(
                    AtlasProcessingProfileRevisionRow.id
                    == _compound_id(profile_id, revision)
                )
            )
            return (
                cast(
                    ProcessingProfileRevision,
                    _record(row, ProcessingProfileRevision),
                )
                if row is not None
                else None
            )

    def list_profile_revisions(
        self,
        *,
        profile_id: str | None = None,
        limit: int = 200,
    ) -> list[ProcessingProfileRevision]:
        _bounded_limit(limit, family="processing profile revision")
        statement = select(AtlasProcessingProfileRevisionRow)
        if profile_id is not None:
            statement = statement.where(
                AtlasProcessingProfileRevisionRow.payload[
                    "profile_id"
                ].as_string()
                == profile_id
            )
        statement = statement.order_by(AtlasProcessingProfileRevisionRow.id).limit(
            limit
        )
        with self.session_factory() as session:
            rows = session.scalars(statement).all()
            return [
                cast(
                    ProcessingProfileRevision,
                    _record(row, ProcessingProfileRevision),
                )
                for row in rows
            ]

    def profile_revisions(self, profile_id: str) -> tuple[ProcessingProfileRevision, ...]:
        """Exact owner-family read; intentionally unbounded by unrelated profiles."""
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasProcessingProfileRevisionRow)
                .where(
                    AtlasProcessingProfileRevisionRow.payload["profile_id"].as_string()
                    == profile_id
                )
                .order_by(AtlasProcessingProfileRevisionRow.id)
            ).all()
            return tuple(
                cast(ProcessingProfileRevision, _record(row, ProcessingProfileRevision))
                for row in rows
            )

    def active_profile_revisions(self) -> tuple[ProcessingProfileRevision, ...]:
        """Rule-specific candidates for reference and active MIME conflict checks."""
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasProcessingProfileRevisionRow)
                .where(
                    AtlasProcessingProfileRevisionRow.payload["status"].as_string()
                    == "active"
                )
                .order_by(AtlasProcessingProfileRevisionRow.id)
            ).all()
            return tuple(
                cast(ProcessingProfileRevision, _record(row, ProcessingProfileRevision))
                for row in rows
            )

    def get_run(self, run_id: str) -> ProcessingRun | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasProcessingRunRow).where(
                    AtlasProcessingRunRow.id == run_id
                )
            )
            return (
                cast(ProcessingRun, _record(row, ProcessingRun))
                if row is not None
                else None
            )

    def list_runs(self, *, limit: int = 200) -> list[ProcessingRun]:
        _bounded_limit(limit, family="processing run")
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasProcessingRunRow)
                .order_by(AtlasProcessingRunRow.id)
                .limit(limit)
            ).all()
            return [
                cast(ProcessingRun, _record(row, ProcessingRun))
                for row in rows
            ]

    def runs_for_document(self, document_id: str) -> tuple[ProcessingRun, ...]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AtlasProcessingRunRow)
                .where(
                    AtlasProcessingRunRow.payload["document_id"].as_string()
                    == document_id
                )
                .order_by(AtlasProcessingRunRow.id)
            ).all()
            return tuple(
                cast(ProcessingRun, _record(row, ProcessingRun)) for row in rows
            )

    def get_replay(self, idempotency_key: str) -> ProcessingIdempotencyRecord | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AtlasProcessingIdempotencyRow).where(
                    AtlasProcessingIdempotencyRow.id == idempotency_key
                )
            )
            return (
                cast(
                    ProcessingIdempotencyRecord,
                    _record(row, ProcessingIdempotencyRecord),
                )
                if row is not None
                else None
            )

    def _list_run_children(
        self, row_type: type, record_type: type, run_id: str
    ) -> tuple:
        with self.session_factory() as session:
            rows = session.scalars(
                select(row_type)
                .where(row_type.payload["run_id"].as_string() == run_id)
                .order_by(row_type.id)
            ).all()
            return tuple(_record(row, record_type) for row in rows)

    def list_parser_invocations(self, run_id: str) -> tuple[ParserAdapterInvocation, ...]:
        return self._list_run_children(
            AtlasParserAdapterInvocationRow, ParserAdapterInvocation, run_id
        )

    def list_source_regions(self, run_id: str) -> tuple[SourceRegion, ...]:
        return self._list_run_children(AtlasSourceRegionRow, SourceRegion, run_id)

    def list_extraction_candidates(self, run_id: str) -> tuple[ExtractionCandidate, ...]:
        return self._list_run_children(
            AtlasExtractionCandidateRow, ExtractionCandidate, run_id
        )

    def list_candidate_groups(self, run_id: str) -> tuple[CandidateGroup, ...]:
        return self._list_run_children(AtlasCandidateGroupRow, CandidateGroup, run_id)

    def list_promotion_decisions(self, run_id: str) -> tuple[PromotionDecision, ...]:
        return self._list_run_children(
            AtlasPromotionDecisionRow, PromotionDecision, run_id
        )

    def list_kpel_handoffs(self, run_id: str) -> tuple[KPELNormalizationHandoff, ...]:
        return self._list_run_children(
            AtlasKpelHandoffRow, KPELNormalizationHandoff, run_id
        )

    def list_routing_decisions(self, run_id: str) -> tuple[RoutingDecision, ...]:
        return self._list_run_children(
            AtlasProcessingRoutingDecisionRow, RoutingDecision, run_id
        )

    def list_evidence_traces(self, run_id: str) -> tuple[EvidenceBuildTrace, ...]:
        return self._list_run_children(
            AtlasEvidenceBuildTraceRow, EvidenceBuildTrace, run_id
        )


@dataclass(frozen=True, slots=True)
class PluginPackageIntent:
    replay: ProcessingIdempotencyRecord | None


@dataclass(frozen=True, slots=True)
class BeginPluginPackageIntentCommand:
    session_factory: SessionFactory

    def execute(self, idempotency_key: str) -> PluginPackageIntent:
        reader = ProcessingRegistryReadModel(self.session_factory)
        return PluginPackageIntent(reader.get_replay(idempotency_key))


@dataclass(frozen=True, slots=True)
class PluginLifecycleIntent:
    replay: ProcessingIdempotencyRecord | None
    plugin_version: PluginVersionRecord | None
    package: PluginPackageRecord | None


@dataclass(frozen=True, slots=True)
class BeginPluginLifecycleIntentCommand:
    session_factory: SessionFactory

    def execute(self, idempotency_key: str, plugin_id: str, version: str) -> PluginLifecycleIntent:
        reader = ProcessingRegistryReadModel(self.session_factory)
        plugin = reader.get_plugin_version(plugin_id, version)
        package = None
        if plugin is not None:
            with self.session_factory() as session:
                row = session.scalar(
                    select(AtlasPluginPackageRow)
                    .where(
                        AtlasPluginPackageRow.payload["plugin_id"].as_string() == plugin_id,
                        AtlasPluginPackageRow.payload["plugin_version"].as_string() == version,
                        AtlasPluginPackageRow.payload["package_digest"].as_string() == plugin.package_digest,
                    )
                    .limit(1)
                )
                package = cast(PluginPackageRecord, _record(row, PluginPackageRecord)) if row is not None else None
        return PluginLifecycleIntent(reader.get_replay(idempotency_key), plugin, package)


@dataclass(frozen=True, slots=True)
class ProcessingProfileIntent:
    replay: ProcessingIdempotencyRecord | None
    profile: ProcessingProfile | None
    revisions: tuple[ProcessingProfileRevision, ...]


@dataclass(frozen=True, slots=True)
class BeginProcessingProfileIntentCommand:
    session_factory: SessionFactory

    def execute(self, idempotency_key: str, profile_id: str) -> ProcessingProfileIntent:
        reader = ProcessingRegistryReadModel(self.session_factory)
        return ProcessingProfileIntent(
            reader.get_replay(idempotency_key),
            reader.get_processing_profile(profile_id),
            reader.profile_revisions(profile_id),
        )


@dataclass(frozen=True, slots=True)
class ProcessingRunIntent:
    replay: ProcessingIdempotencyRecord | None
    run: ProcessingRun | None
    parser_invocations: tuple[ParserAdapterInvocation, ...]
    source_regions: tuple[SourceRegion, ...]
    extraction_candidates: tuple[ExtractionCandidate, ...]
    candidate_groups: tuple[CandidateGroup, ...]
    promotion_decisions: tuple[PromotionDecision, ...]
    kpel_handoffs: tuple[KPELNormalizationHandoff, ...]
    routing_decisions: tuple[RoutingDecision, ...]
    evidence_traces: tuple[EvidenceBuildTrace, ...]


@dataclass(frozen=True, slots=True)
class BeginProcessingRunIntentCommand:
    session_factory: SessionFactory

    def execute(self, idempotency_key: str, run_id: str | None) -> ProcessingRunIntent:
        reader = ProcessingRegistryReadModel(self.session_factory)
        if run_id is None:
            return ProcessingRunIntent(
                reader.get_replay(idempotency_key), None, (), (), (), (), (), (), (), ()
            )
        return ProcessingRunIntent(
            reader.get_replay(idempotency_key), reader.get_run(run_id),
            reader.list_parser_invocations(run_id),
            reader.list_source_regions(run_id),
            reader.list_extraction_candidates(run_id),
            reader.list_candidate_groups(run_id),
            reader.list_promotion_decisions(run_id),
            reader.list_kpel_handoffs(run_id),
            reader.list_routing_decisions(run_id),
            reader.list_evidence_traces(run_id),
        )


@dataclass(frozen=True, slots=True)
class FinalizePluginPackageInput:
    packages: tuple[PluginPackageRecord, ...]
    plugin_versions: tuple[PluginVersionWrite, ...]
    idempotency_record: ProcessingIdempotencyRecord
    audit_events: tuple[AuditEventRecord, ...]


@dataclass(frozen=True, slots=True)
class FinalizePluginPackageCommand:
    session_factory: SessionFactory

    def execute(self, request: FinalizePluginPackageInput) -> None:
        _ProcessingCommandCoordinator(self.session_factory)._finalize(
            _ProcessingRegistryWriteBatch(
                packages=request.packages,
                plugin_versions=request.plugin_versions,
                idempotency_records=(request.idempotency_record,),
                audit_events=request.audit_events,
            )
        )


@dataclass(frozen=True, slots=True)
class FinalizePluginLifecycleInput:
    plugin_versions: tuple[PluginVersionWrite, ...]
    idempotency_record: ProcessingIdempotencyRecord
    audit_events: tuple[AuditEventRecord, ...]
    disable_precondition: PluginDisablePrecondition | None = None


@dataclass(frozen=True, slots=True)
class FinalizePluginLifecycleCommand:
    session_factory: SessionFactory

    def execute(self, request: FinalizePluginLifecycleInput) -> None:
        _ProcessingCommandCoordinator(self.session_factory)._finalize(
            _ProcessingRegistryWriteBatch(
                plugin_versions=request.plugin_versions,
                idempotency_records=(request.idempotency_record,),
                audit_events=request.audit_events,
                plugin_disable_precondition=request.disable_precondition,
            )
        )


@dataclass(frozen=True, slots=True)
class FinalizeProcessingProfileInput:
    profiles: tuple[ProcessingProfile, ...]
    revisions: tuple[ProcessingProfileRevisionWrite, ...]
    idempotency_record: ProcessingIdempotencyRecord
    audit_events: tuple[AuditEventRecord, ...]
    activation_precondition: ProfileActivationPrecondition | None = None


@dataclass(frozen=True, slots=True)
class FinalizeProcessingProfileCommand:
    session_factory: SessionFactory

    def execute(self, request: FinalizeProcessingProfileInput) -> None:
        _ProcessingCommandCoordinator(self.session_factory)._finalize(
            _ProcessingRegistryWriteBatch(
                processing_profiles=request.profiles,
                profile_revisions=request.revisions,
                idempotency_records=(request.idempotency_record,),
                audit_events=request.audit_events,
                profile_activation_precondition=request.activation_precondition,
            )
        )


@dataclass(frozen=True, slots=True)
class FinalizeProcessingRunInput:
    runs: tuple[ProcessingRunWrite, ...]
    idempotency_record: ProcessingIdempotencyRecord
    audit_events: tuple[AuditEventRecord, ...]
    parser_invocations: tuple[ParserInvocationWrite, ...] = ()
    source_regions: tuple[SourceRegionWrite, ...] = ()
    extraction_candidates: tuple[ExtractionCandidateWrite, ...] = ()
    candidate_groups: tuple[CandidateGroupWrite, ...] = ()
    promotion_decisions: tuple[PromotionDecisionWrite, ...] = ()
    kpel_handoffs: tuple[KpelHandoffWrite, ...] = ()
    routing_decisions: tuple[RoutingDecisionWrite, ...] = ()
    evidence_traces: tuple[EvidenceTraceWrite, ...] = ()


@dataclass(frozen=True, slots=True)
class FinalizeProcessingRunCommand:
    session_factory: SessionFactory

    def execute(self, request: FinalizeProcessingRunInput) -> None:
        _ProcessingCommandCoordinator(self.session_factory)._finalize(
            _ProcessingRegistryWriteBatch(
                runs=request.runs,
                parser_invocations=request.parser_invocations,
                source_regions=request.source_regions,
                extraction_candidates=request.extraction_candidates,
                candidate_groups=request.candidate_groups,
                promotion_decisions=request.promotion_decisions,
                kpel_handoffs=request.kpel_handoffs,
                routing_decisions=request.routing_decisions,
                evidence_traces=request.evidence_traces,
                idempotency_records=(request.idempotency_record,),
                audit_events=request.audit_events,
            )
        )


__all__ = [
    "BeginPluginPackageIntentCommand",
    "BeginPluginLifecycleIntentCommand",
    "BeginProcessingProfileIntentCommand",
    "BeginProcessingRunIntentCommand",
    "FinalizePluginLifecycleCommand",
    "FinalizePluginLifecycleInput",
    "FinalizePluginPackageCommand",
    "FinalizePluginPackageInput",
    "FinalizeProcessingProfileCommand",
    "FinalizeProcessingProfileInput",
    "FinalizeProcessingRunCommand",
    "FinalizeProcessingRunInput",
    "ParserInvocationWrite",
    "SourceRegionWrite",
    "ExtractionCandidateWrite",
    "CandidateGroupWrite",
    "PromotionDecisionWrite",
    "KpelHandoffWrite",
    "RoutingDecisionWrite",
    "EvidenceTraceWrite",
    "PluginVersionWrite",
    "PluginDisablePrecondition",
    "PluginActivationDependency",
    "ProfileActivationPrecondition",
    "ProcessingProfileRevisionWrite",
    "ProcessingRegistryCurrentnessConflict",
    "PluginPackageIntent",
    "PluginLifecycleIntent",
    "ProcessingProfileIntent",
    "ProcessingRunIntent",
    "ProcessingRegistryReadModel",
    "ProcessingRunWrite",
]
