from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Callable, Protocol

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from atlas_production.infrastructure.postgres_audit_adapter import build_audit_event
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter

from atlas_production.infrastructure.persistence.payload_policy import (
    GENERAL_METADATA_MAX_BYTES,
    PersistedPayloadPolicyError,
    validate_typed_payload,
)
from atlas_production.infrastructure.persistence.skill_designer import (
    AtlasSkillCandidateRow,
    AtlasSkillCandidateIdempotencyRow,
    AtlasSkillDesignerCheckpointRow,
    AtlasSkillDesignRunRow,
)
from atlas_production.modules.consolidator.public import (
    ConsolidationCursorV1,
    ConsolidationReader,
    ConsolidationV1,
)
from atlas_production.modules.prompt_skills.public import (
    PromptSkillApprovedPublishV1,
    PromptSkillCategory,
    PromptSkillError,
    PromptSkillMutationOutcomeV1,
)
from atlas_production.modules.skill_designer.public import (
    SCHEMA_VERSION,
    SKILL_DESIGNER_PROMPT_REVISION,
    ApproveSkillCandidateV1,
    RejectSkillCandidateV1,
    SkillCandidateStoreError,
    SkillCandidateMutationOutcomeV1,
    SkillCandidateDetailV1,
    SkillCandidateDraftV1,
    SkillCandidateListV1,
    SkillCandidateSummaryV1,
    SkillDesignRunClaimV1,
    SkillDesignRunV1,
    SkillDesignSourceV1,
    SkillDesignerOwner,
    skill_design_run_ref,
    skill_design_result_digest,
)

SessionFactory = Callable[[], Session]
_RETRY_DELAY = timedelta(seconds=30)
_DRAFT_FIELDS = frozenset(SkillCandidateDraftV1.model_fields)



class _ApprovedPublisher(Protocol):
    def publish_enabled_in_session(
        self,
        session: Session,
        *,
        actor_id: str,
        request: PromptSkillApprovedPublishV1,
    ) -> PromptSkillMutationOutcomeV1: ...

class SkillDesignerConflict(RuntimeError):
    """Source, claim, candidate preimage, or replay conflict."""


class SkillDesignerClaimLost(SkillDesignerConflict):
    """The supplied Skill Designer claim is no longer live."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _safe_identity(value: str, field_name: str, maximum: int) -> None:
    if not value or len(value) > maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum} characters")


def _lease(lease_seconds: int) -> None:
    if lease_seconds < 1 or lease_seconds > 3600:
        raise ValueError("Skill Designer lease seconds must be between 1 and 3600")


def _source(consolidation: ConsolidationV1) -> SkillDesignSourceV1:
    return SkillDesignSourceV1(
        consolidation_ref=consolidation.consolidation_ref,
        consolidation_digest=consolidation.digest,
        consolidation_scan_sequence=consolidation.scan_sequence,
    )


def _source_from_row(row: AtlasSkillDesignRunRow) -> SkillDesignSourceV1:
    return SkillDesignSourceV1(
        consolidation_ref=row.consolidation_ref,
        consolidation_digest=row.consolidation_digest,
        consolidation_scan_sequence=row.consolidation_scan_sequence,
    )


def _run_from_row(row: AtlasSkillDesignRunRow) -> SkillDesignRunV1:
    try:
        return SkillDesignRunV1(
            run_ref=row.run_ref,
            source=_source_from_row(row),
            status=row.status,
            attempt=row.attempt,
            fence=row.fence,
            pinned_route_id=row.pinned_route_id,
            pinned_route_revision=row.pinned_route_revision,
            pinned_runtime_policy_revision=row.pinned_runtime_policy_revision,
            model_invocation_refs=list(row.model_invocation_refs),
            result_digest=row.result_digest,
            failure_code=row.failure_code,
            next_attempt_at=row.next_attempt_at,
            candidate_refs=list(row.candidate_refs),
            candidate_material_digests=list(row.candidate_material_digests),
            completed_at=row.completed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise SkillDesignerConflict("persisted Skill Designer run is invalid") from exc


def _claim_from_row(row: AtlasSkillDesignRunRow) -> SkillDesignRunClaimV1:
    if row.claim_token is None or row.lease_expires_at is None:
        raise SkillDesignerConflict("Skill Designer run lacks a live claim")
    return SkillDesignRunClaimV1(
        run_ref=row.run_ref,
        source=_source_from_row(row),
        attempt=row.attempt,
        fence=row.fence,
        claim_token=row.claim_token,
        lease_expires_at=row.lease_expires_at,
        pinned_route_id=row.pinned_route_id,
        pinned_route_revision=row.pinned_route_revision,
        pinned_runtime_policy_revision=row.pinned_runtime_policy_revision,
    )


def _claim_matches(row: AtlasSkillDesignRunRow, claim: SkillDesignRunClaimV1) -> bool:
    return (
        row.run_ref == claim.run_ref
        and _source_from_row(row) == claim.source
        and row.attempt == claim.attempt
        and row.fence == claim.fence
        and row.claim_token == claim.claim_token
    )


def _lock_claim(
    session: Session, claim: SkillDesignRunClaimV1, observed_at: datetime
) -> AtlasSkillDesignRunRow:
    row = session.scalar(
        select(AtlasSkillDesignRunRow)
        .where(AtlasSkillDesignRunRow.run_ref == claim.run_ref)
        .with_for_update()
    )
    if (
        row is None
        or not _claim_matches(row, claim)
        or row.status != "designing"
        or row.lease_expires_at is None
        or row.lease_expires_at <= observed_at
        or row.lease_expires_at != claim.lease_expires_at
        or row.pinned_route_id != claim.pinned_route_id
        or row.pinned_route_revision != claim.pinned_route_revision
        or row.pinned_runtime_policy_revision != claim.pinned_runtime_policy_revision
    ):
        raise SkillDesignerClaimLost("Skill Designer claim is no longer live")
    return row


def _candidate_ref(draft_key: str) -> str:
    return f"skill-candidate:{hashlib.sha256(draft_key.encode('utf-8')).hexdigest()}:v1"


def _validated_draft(draft: SkillCandidateDraftV1) -> tuple[dict, str]:
    raw = draft.model_dump(mode="json")
    try:
        payload = validate_typed_payload(
            raw,
            family="skill_candidate_draft_v1",
            allowed_fields=_DRAFT_FIELDS,
            max_bytes=GENERAL_METADATA_MAX_BYTES,
        )
    except PersistedPayloadPolicyError as exc:
        raise SkillDesignerConflict("skill_candidate_payload_invalid") from exc
    material = {
        key: value
        for key, value in payload.items()
        if key != "source_evidence"
    }
    return payload, hashlib.sha256(_canonical(material)).hexdigest()

def _merge_candidate_row(
    row: AtlasSkillCandidateRow,
    *,
    expected_ref: str,
    draft: SkillCandidateDraftV1,
    payload: dict,
    material_digest: str,
    observed_at: datetime,
) -> bool:
    if row.candidate_ref != expected_ref:
        raise SkillDesignerConflict("skill_candidate_identity_conflict")
    if row.material_digest == material_digest:
        return False
    if row.status == "applying":
        raise SkillDesignerConflict("skill_candidate_apply_in_progress")
    row.disposition = draft.disposition
    row.category = draft.category
    row.target_name = draft.target_name
    row.topic = draft.topic
    row.goal = draft.goal
    row.draft_revision += 1
    row.status = "draft"
    row.draft_payload = payload
    row.material_digest = material_digest
    row.skill_source_digest = draft.skill_source_digest
    row.approved_skill_ref = None
    row.updated_at = observed_at
    return True



def _summary(row: AtlasSkillCandidateRow) -> SkillCandidateSummaryV1:
    try:
        return SkillCandidateSummaryV1(
            candidate_ref=row.candidate_ref,
            draft_key=row.draft_key,
            disposition=row.disposition,
            category=row.category,
            target_name=row.target_name,
            topic=row.topic,
            goal=row.goal,
            draft_revision=row.draft_revision,
            status=row.status,
            skill_source_digest=row.skill_source_digest,
            updated_at=row.updated_at,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise SkillDesignerConflict("persisted Skill candidate summary is invalid") from exc


def _detail(row: AtlasSkillCandidateRow) -> SkillCandidateDetailV1:
    try:
        draft = SkillCandidateDraftV1.model_validate(row.draft_payload)
        return SkillCandidateDetailV1(
            **_summary(row).model_dump(),
            source_evidence=draft.source_evidence,
            observed_catalog_refs=draft.observed_catalog_refs,
            matched_skill_refs=draft.matched_skill_refs,
            skill_source=draft.skill_source,
            rationale=draft.rationale,
            risk=draft.risk,
            approved_skill_ref=row.approved_skill_ref,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise SkillDesignerConflict("persisted Skill candidate detail is invalid") from exc


class PostgresSkillDesignerOwner(SkillDesignerOwner):
    def __init__(
        self,
        session_factory: SessionFactory,
        publisher: _ApprovedPublisher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher

    @staticmethod
    def _candidate_replay(
        session: Session,
        *,
        actor_id: str,
        candidate_ref: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
    ) -> SkillCandidateMutationOutcomeV1 | None:
        row = session.get(AtlasSkillCandidateIdempotencyRow, idempotency_key)
        if row is None:
            return None
        if (
            row.actor_id != actor_id
            or row.candidate_ref != candidate_ref
            or row.operation != operation
            or row.request_digest != request_digest
        ):
            candidate = session.get(AtlasSkillCandidateRow, candidate_ref)
            if candidate is None:
                raise SkillCandidateStoreError("skill_candidate_idempotency_conflict")
            detail = _detail(candidate)
            return SkillCandidateMutationOutcomeV1(
                candidate_ref=detail.candidate_ref,
                draft_revision=detail.draft_revision,
                status=detail.status,
                outcome="conflict",
                approved_skill_ref=detail.approved_skill_ref,
            )
        stored = SkillCandidateMutationOutcomeV1.model_validate(row.response_payload)
        return stored.model_copy(update={"outcome": "replayed"})

    @staticmethod
    def _store_candidate_replay(
        session: Session,
        *,
        actor_id: str,
        candidate_ref: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
        outcome: SkillCandidateMutationOutcomeV1,
        observed_at: datetime,
    ) -> None:
        session.add(
            AtlasSkillCandidateIdempotencyRow(
                idempotency_key=idempotency_key,
                operation=operation,
                actor_id=actor_id,
                candidate_ref=candidate_ref,
                request_digest=request_digest,
                response_payload=outcome.model_dump(mode="json"),
                created_at=observed_at,
            )
        )

    def register_consolidation(self, consolidation: ConsolidationV1) -> SkillDesignRunV1:
        source = _source(consolidation)
        ref = skill_design_run_ref(
            consolidation_ref=source.consolidation_ref,
            consolidation_digest=source.consolidation_digest,
        )
        with self._session_factory() as session, session.begin():
            session.execute(
                pg_insert(AtlasSkillDesignRunRow)
                .values(
                    run_ref=ref,
                    schema_version=SCHEMA_VERSION,
                    prompt_revision=SKILL_DESIGNER_PROMPT_REVISION,
                    consolidation_ref=source.consolidation_ref,
                    consolidation_digest=source.consolidation_digest,
                    consolidation_scan_sequence=source.consolidation_scan_sequence,
                    status="pending",
                    result_digest=None,
                    attempt=0,
                    fence=0,
                    worker_id=None,
                    claim_token=None,
                    lease_expires_at=None,
                    next_attempt_at=None,
                    pinned_route_id=None,
                    pinned_route_revision=None,
                    pinned_runtime_policy_revision=None,
                    model_invocation_refs=[],
                    failure_code=None,
                    candidate_refs=[],
                    candidate_material_digests=[],
                    created_at=consolidation.completed_at,
                    updated_at=consolidation.completed_at,
                    completed_at=None,
                )
                .on_conflict_do_nothing(index_elements=[AtlasSkillDesignRunRow.run_ref])
            )
            row = session.scalar(
                select(AtlasSkillDesignRunRow)
                .where(AtlasSkillDesignRunRow.run_ref == ref)
                .with_for_update()
            )
            if row is None or _source_from_row(row) != source:
                raise SkillDesignerConflict("skill_design_registration_conflict")
            return _run_from_row(row)

    def register_completed_after(
        self,
        reader: ConsolidationReader,
        cursor: ConsolidationCursorV1 | None,
        limit: int,
    ) -> list[SkillDesignRunV1]:
        if limit < 1 or limit > 100:
            raise ValueError("Skill Designer registration limit must be between 1 and 100")
        with self._session_factory() as session, session.begin():
            session.execute(
                pg_insert(AtlasSkillDesignerCheckpointRow)
                .values(
                    checkpoint_key="global",
                    last_scan_sequence=None,
                    last_consolidation_ref=None,
                    updated_at=datetime.now().astimezone(),
                )
                .on_conflict_do_nothing(
                    index_elements=[AtlasSkillDesignerCheckpointRow.checkpoint_key]
                )
            )
            checkpoint = session.scalar(
                select(AtlasSkillDesignerCheckpointRow)
                .where(AtlasSkillDesignerCheckpointRow.checkpoint_key == "global")
                .with_for_update()
            )
            if checkpoint is None:
                raise SkillDesignerConflict("Skill Designer checkpoint unavailable")
            persisted_cursor = (
                None
                if checkpoint.last_scan_sequence is None
                else ConsolidationCursorV1(
                    scan_sequence=checkpoint.last_scan_sequence,
                    consolidation_ref=checkpoint.last_consolidation_ref,
                )
            )
            if cursor is not None and cursor != persisted_cursor:
                raise SkillDesignerConflict("skill_design_cursor_conflict")
            consolidations = reader.list_consolidations_after(persisted_cursor, limit)
            runs: list[SkillDesignRunV1] = []
            for consolidation in consolidations:
                source = _source(consolidation)
                ref = skill_design_run_ref(
                    consolidation_ref=source.consolidation_ref,
                    consolidation_digest=source.consolidation_digest,
                )
                session.execute(
                    pg_insert(AtlasSkillDesignRunRow)
                    .values(
                        run_ref=ref,
                        schema_version=SCHEMA_VERSION,
                        prompt_revision=SKILL_DESIGNER_PROMPT_REVISION,
                        consolidation_ref=source.consolidation_ref,
                        consolidation_digest=source.consolidation_digest,
                        consolidation_scan_sequence=source.consolidation_scan_sequence,
                        status="pending",
                        result_digest=None,
                        attempt=0,
                        fence=0,
                        worker_id=None,
                        claim_token=None,
                        lease_expires_at=None,
                        next_attempt_at=None,
                        pinned_route_id=None,
                        pinned_route_revision=None,
                        pinned_runtime_policy_revision=None,
                        model_invocation_refs=[],
                        failure_code=None,
                        candidate_refs=[],
                        candidate_material_digests=[],
                        created_at=consolidation.completed_at,
                        updated_at=consolidation.completed_at,
                        completed_at=None,
                    )
                    .on_conflict_do_nothing(index_elements=[AtlasSkillDesignRunRow.run_ref])
                )
                row = session.scalar(
                    select(AtlasSkillDesignRunRow)
                    .where(AtlasSkillDesignRunRow.run_ref == ref)
                    .with_for_update()
                )
                if row is None or _source_from_row(row) != source:
                    raise SkillDesignerConflict("skill_design_registration_conflict")
                runs.append(_run_from_row(row))
            if consolidations:
                last = consolidations[-1]
                checkpoint.last_scan_sequence = last.scan_sequence
                checkpoint.last_consolidation_ref = last.consolidation_ref
                checkpoint.updated_at = last.completed_at
            session.flush()
            return runs

    def claim_next(
        self, worker_id: str, observed_at: datetime, lease_seconds: int = 300
    ) -> SkillDesignRunClaimV1 | None:
        _safe_identity(worker_id, "worker_id", 200)
        _lease(lease_seconds)
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AtlasSkillDesignRunRow)
                .where(
                    or_(
                        AtlasSkillDesignRunRow.status == "pending",
                        (
                            (AtlasSkillDesignRunRow.status == "retryable_failed")
                            & (AtlasSkillDesignRunRow.next_attempt_at <= observed_at)
                        ),
                        (
                            (AtlasSkillDesignRunRow.status == "designing")
                            & (AtlasSkillDesignRunRow.lease_expires_at <= observed_at)
                        ),
                    )
                )
                .order_by(
                    AtlasSkillDesignRunRow.consolidation_scan_sequence,
                    AtlasSkillDesignRunRow.run_ref,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.status = "designing"
            row.attempt += 1
            row.fence += 1
            row.worker_id = worker_id
            row.claim_token = uuid.uuid4().hex
            row.lease_expires_at = observed_at + timedelta(seconds=lease_seconds)
            row.next_attempt_at = None
            row.pinned_route_id = None
            row.pinned_route_revision = None
            row.pinned_runtime_policy_revision = None
            row.failure_code = None
            row.updated_at = observed_at
            session.flush()
            return _claim_from_row(row)

    def pin_route(
        self,
        claim: SkillDesignRunClaimV1,
        route_id: str,
        route_revision: int,
        runtime_policy_revision: int,
        observed_at: datetime,
    ) -> SkillDesignRunClaimV1:
        _safe_identity(route_id, "route_id", 200)
        if route_revision < 1 or runtime_policy_revision < 1:
            raise ValueError("Skill Designer route revisions must be positive")
        with self._session_factory() as session, session.begin():
            row = _lock_claim(session, claim, observed_at)
            current = (
                row.pinned_route_id,
                row.pinned_route_revision,
                row.pinned_runtime_policy_revision,
            )
            requested = (route_id, route_revision, runtime_policy_revision)
            if current == (None, None, None):
                (
                    row.pinned_route_id,
                    row.pinned_route_revision,
                    row.pinned_runtime_policy_revision,
                ) = requested
                row.updated_at = observed_at
                session.flush()
            elif current != requested:
                raise SkillDesignerConflict("model_route_revision_conflict")
            return _claim_from_row(row)

    def renew_claim(
        self,
        claim: SkillDesignRunClaimV1,
        observed_at: datetime,
        lease_seconds: int = 300,
    ) -> SkillDesignRunClaimV1:
        _lease(lease_seconds)
        with self._session_factory() as session, session.begin():
            row = _lock_claim(session, claim, observed_at)
            row.lease_expires_at = observed_at + timedelta(seconds=lease_seconds)
            row.updated_at = observed_at
            session.flush()
            return _claim_from_row(row)

    def complete(
        self,
        claim: SkillDesignRunClaimV1,
        drafts: list[SkillCandidateDraftV1],
        model_invocation_refs: list[str],
        observed_at: datetime,
    ) -> SkillDesignRunV1:
        keys = [draft.draft_key for draft in drafts]
        if len(set(keys)) != len(keys):
            raise SkillDesignerConflict("skill_candidate_duplicate_draft_key")
        if not model_invocation_refs or len(set(model_invocation_refs)) != len(
            model_invocation_refs
        ):
            raise SkillDesignerConflict("skill_design_invocation_provenance_invalid")
        if any(
            evidence.consolidation_ref != claim.source.consolidation_ref
            or evidence.consolidation_digest != claim.source.consolidation_digest
            for draft in drafts
            for evidence in draft.source_evidence
        ):
            raise SkillDesignerConflict("skill_candidate_source_integrity_conflict")
        with self._session_factory() as session, session.begin():
            run = _lock_claim(session, claim, observed_at)
            refs: list[str] = []
            material_digests: list[str] = []
            for draft in drafts:
                row = None
                if draft.candidate_ref is not None:
                    row = session.scalar(
                        select(AtlasSkillCandidateRow)
                        .where(
                            AtlasSkillCandidateRow.candidate_ref
                            == draft.candidate_ref
                        )
                        .with_for_update()
                    )
                    if (
                        row is None
                        or row.draft_key != draft.draft_key
                        or row.disposition != draft.disposition
                        or row.category != draft.category
                        or row.target_name != draft.target_name
                    ):
                        raise SkillDesignerConflict(
                            "skill_candidate_identity_conflict"
                        )
                    ref = row.candidate_ref
                else:
                    ref = _candidate_ref(draft.draft_key)
                    row = session.scalar(
                        select(AtlasSkillCandidateRow)
                        .where(
                            AtlasSkillCandidateRow.draft_key
                            == draft.draft_key
                        )
                        .with_for_update()
                    )
                effective = draft.model_copy(update={"candidate_ref": ref})
                payload, material_digest = _validated_draft(effective)
                if row is None:
                    row = AtlasSkillCandidateRow(
                        candidate_ref=ref,
                        draft_key=draft.draft_key,
                        disposition=draft.disposition,
                        category=draft.category,
                        target_name=draft.target_name,
                        topic=draft.topic,
                        goal=draft.goal,
                        draft_revision=1,
                        status="draft",
                        draft_payload=payload,
                        material_digest=material_digest,
                        skill_source_digest=draft.skill_source_digest,
                        approved_skill_ref=None,
                        created_at=observed_at,
                        updated_at=observed_at,
                    )
                    session.add(row)
                else:
                    _merge_candidate_row(
                        row,
                        expected_ref=ref,
                        draft=effective,
                        payload=payload,
                        material_digest=material_digest,
                        observed_at=observed_at,
                    )
                refs.append(ref)
                material_digests.append(material_digest)
            run.result_digest = skill_design_result_digest(
                source=claim.source,
                candidate_refs=refs,
                candidate_material_digests=material_digests,
                model_invocation_refs=model_invocation_refs,
            )
            run.status = "completed"
            run.worker_id = None
            run.claim_token = None
            run.lease_expires_at = None
            run.next_attempt_at = None
            run.failure_code = None
            run.model_invocation_refs = list(model_invocation_refs)
            run.candidate_refs = refs
            run.candidate_material_digests = material_digests
            run.updated_at = observed_at
            run.completed_at = observed_at
            session.flush()
            return _run_from_row(run)

    def fail(
        self,
        claim: SkillDesignRunClaimV1,
        failure_code: str,
        retryable: bool,
        observed_at: datetime,
    ) -> SkillDesignRunV1:
        _safe_identity(failure_code, "failure_code", 100)
        with self._session_factory() as session, session.begin():
            row = _lock_claim(session, claim, observed_at)
            row.status = "retryable_failed" if retryable else "failed"
            row.worker_id = None
            row.claim_token = None
            row.lease_expires_at = None
            row.next_attempt_at = observed_at + _RETRY_DELAY if retryable else None
            row.pinned_route_id = None if retryable else row.pinned_route_id
            row.pinned_route_revision = None if retryable else row.pinned_route_revision
            row.model_invocation_refs = []
            row.result_digest = None
            row.pinned_runtime_policy_revision = (
                None if retryable else row.pinned_runtime_policy_revision
            )
            row.failure_code = failure_code
            row.updated_at = observed_at
            session.flush()
            return _run_from_row(row)

    def list_candidate_summaries(
        self, category: PromptSkillCategory | None = None
    ) -> SkillCandidateListV1:
        with self._session_factory() as session:
            statement = select(AtlasSkillCandidateRow).order_by(
                AtlasSkillCandidateRow.updated_at.desc(),
                AtlasSkillCandidateRow.candidate_ref,
            )
            if category is not None:
                statement = statement.where(AtlasSkillCandidateRow.category == category)
            return SkillCandidateListV1(
                items=[_summary(row) for row in session.scalars(statement)]
            )

    def read_candidate(self, candidate_ref: str) -> SkillCandidateDetailV1 | None:
        _safe_identity(candidate_ref, "candidate_ref", 300)
        with self._session_factory() as session:
            row = session.get(AtlasSkillCandidateRow, candidate_ref)
            return None if row is None else _detail(row)

    def reject_candidate(
        self,
        actor_id: str,
        candidate_ref: str,
        command: RejectSkillCandidateV1,
    ) -> SkillCandidateMutationOutcomeV1:
        _safe_identity(actor_id, "actor_id", 200)
        digest = hashlib.sha256(
            _canonical(
                {
                    "operation": "reject",
                    "actor_id": actor_id,
                    "candidate_ref": candidate_ref,
                    "expected_draft_revision": command.expected_draft_revision,
                }
            )
        ).hexdigest()
        observed_at = datetime.now().astimezone()
        with self._session_factory() as session, session.begin():
            acquire_owner_locks(
                session,
                domain_keys=(
                    f"skill-candidates:idempotency:{command.idempotency_key}",
                    f"skill-candidates:{candidate_ref}",
                ),
            )
            replay = self._candidate_replay(
                session,
                actor_id=actor_id,
                candidate_ref=candidate_ref,
                operation="reject",
                idempotency_key=command.idempotency_key,
                request_digest=digest,
            )
            if replay is not None:
                return replay
            row = session.scalar(
                select(AtlasSkillCandidateRow)
                .where(AtlasSkillCandidateRow.candidate_ref == candidate_ref)
                .with_for_update()
            )
            if row is None:
                raise SkillCandidateStoreError("skill_candidate_not_found")
            if (
                row.draft_revision != command.expected_draft_revision
                or row.status != "draft"
            ):
                raise SkillCandidateStoreError("skill_candidate_precondition_failed")
            row.status = "rejected"
            row.updated_at = observed_at
            outcome = SkillCandidateMutationOutcomeV1(
                candidate_ref=row.candidate_ref,
                draft_revision=row.draft_revision,
                status="rejected",
                outcome="rejected",
            )
            AuditEventWriter(session).append(
                build_audit_event(
                    event_type="skill_candidate_rejected",
                    actor_id=actor_id,
                    target_ref=candidate_ref,
                    project_id=None,
                    message_code="prompt_skills.candidate_was_rejected",
                    metadata={
                        "revision": row.draft_revision,
                        "request_id": command.idempotency_key,
                    },
                )
            )
            self._store_candidate_replay(
                session,
                actor_id=actor_id,
                candidate_ref=candidate_ref,
                operation="reject",
                idempotency_key=command.idempotency_key,
                request_digest=digest,
                outcome=outcome,
                observed_at=observed_at,
            )
            session.flush()
            return outcome

    def approve_candidate(
        self,
        actor_id: str,
        candidate_ref: str,
        command: ApproveSkillCandidateV1,
    ) -> SkillCandidateMutationOutcomeV1:
        _safe_identity(actor_id, "actor_id", 200)
        if self._publisher is None:
            raise SkillCandidateStoreError("skill_candidate_publisher_unavailable")
        digest = hashlib.sha256(
            _canonical(
                {
                    "operation": "approve",
                    "actor_id": actor_id,
                    "candidate_ref": candidate_ref,
                    "expected_draft_revision": command.expected_draft_revision,
                }
            )
        ).hexdigest()
        observed_at = datetime.now().astimezone()
        with self._session_factory() as session, session.begin():
            acquire_owner_locks(
                session,
                domain_keys=(
                    f"skill-candidates:idempotency:{command.idempotency_key}",
                    f"skill-candidates:{candidate_ref}",
                ),
            )
            replay = self._candidate_replay(
                session,
                actor_id=actor_id,
                candidate_ref=candidate_ref,
                operation="approve",
                idempotency_key=command.idempotency_key,
                request_digest=digest,
            )
            if replay is not None:
                return replay
            row = session.scalar(
                select(AtlasSkillCandidateRow)
                .where(AtlasSkillCandidateRow.candidate_ref == candidate_ref)
                .with_for_update()
            )
            if row is None:
                raise SkillCandidateStoreError("skill_candidate_not_found")
            if (
                row.draft_revision != command.expected_draft_revision
                or row.status != "draft"
            ):
                raise SkillCandidateStoreError("skill_candidate_precondition_failed")
            draft = SkillCandidateDraftV1.model_validate(row.draft_payload)
            target_matches = [
                ref
                for ref in draft.matched_skill_refs
                if ref.category == draft.category and ref.name == draft.target_name
            ]
            if draft.disposition == "revise" and len(target_matches) != 1:
                raise SkillCandidateStoreError("skill_candidate_payload_invalid")
            expected_target = (
                target_matches[0] if draft.disposition == "revise" else None
            )
            row.status = "applying"
            row.updated_at = observed_at
            session.flush()
            request = PromptSkillApprovedPublishV1(
                disposition=draft.disposition,
                category=draft.category,
                name=draft.target_name,
                source=draft.skill_source,
                source_digest=draft.skill_source_digest,
                expected_catalogs=draft.observed_catalog_refs,
                expected_target=expected_target,
                idempotency_key=command.idempotency_key,
            )
            try:
                published = self._publisher.publish_enabled_in_session(
                    session,
                    actor_id=actor_id,
                    request=request,
                )
            except PromptSkillError as exc:
                if exc.error_code != "revision_conflict":
                    raise
                row.status = "stale"
                row.updated_at = observed_at
                outcome = SkillCandidateMutationOutcomeV1(
                    candidate_ref=row.candidate_ref,
                    draft_revision=row.draft_revision,
                    status="stale",
                    outcome="stale",
                )
                AuditEventWriter(session).append(
                    build_audit_event(
                        event_type="skill_candidate_stale",
                        actor_id=actor_id,
                        target_ref=candidate_ref,
                        project_id=None,
                        message_code="prompt_skills.candidate_became_stale",
                        metadata={
                            "revision": row.draft_revision,
                            "request_id": command.idempotency_key,
                        },
                    )
                )
                self._store_candidate_replay(
                    session,
                    actor_id=actor_id,
                    candidate_ref=candidate_ref,
                    operation="approve",
                    idempotency_key=command.idempotency_key,
                    request_digest=digest,
                    outcome=outcome,
                    observed_at=observed_at,
                )
                session.flush()
                return outcome
            if published.revision is None:
                raise SkillCandidateStoreError("skill_candidate_publication_invalid")
            row.status = "approved"
            row.approved_skill_ref = published.revision.ref.model_dump(mode="json")
            row.updated_at = observed_at
            outcome = SkillCandidateMutationOutcomeV1(
                candidate_ref=row.candidate_ref,
                draft_revision=row.draft_revision,
                status="approved",
                outcome="approved",
                approved_skill_ref=published.revision.ref,
            )
            AuditEventWriter(session).append(
                build_audit_event(
                    event_type="skill_candidate_approved",
                    actor_id=actor_id,
                    target_ref=candidate_ref,
                    project_id=None,
                    message_code="prompt_skills.candidate_was_approved",
                    metadata={
                        "revision": row.draft_revision,
                        "prompt_skill_ref": (
                            f"prompt-skill:{published.revision.ref.category}:"
                            f"{published.revision.ref.name}:"
                            f"{published.revision.ref.revision}:"
                            f"{published.revision.ref.content_digest}"
                        ),
                        "request_id": command.idempotency_key,
                    },
                )
            )
            self._store_candidate_replay(
                session,
                actor_id=actor_id,
                candidate_ref=candidate_ref,
                operation="approve",
                idempotency_key=command.idempotency_key,
                request_digest=digest,
                outcome=outcome,
                observed_at=observed_at,
            )
            session.flush()
            return outcome


__all__ = [
    "PostgresSkillDesignerOwner",
    "SkillDesignerClaimLost",
    "SkillDesignerConflict",
]
