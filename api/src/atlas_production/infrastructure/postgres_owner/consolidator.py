from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Callable

from pydantic import ValidationError
from sqlalchemy import or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.consolidator import (
    CONSOLIDATION_SCAN_SEQUENCE,
    AtlasConsolidationRunRow,
    AtlasConsolidatorCheckpointRow,
)
from atlas_production.infrastructure.persistence.payload_policy import (
    GENERAL_METADATA_MAX_BYTES,
    PersistedPayloadPolicyError,
    validate_typed_payload,
)
from atlas_production.modules.consolidator.public import (
    CONSOLIDATOR_PROMPT_REVISION,
    SCHEMA_VERSION,
    ConsolidatedExperienceV1,
    ConsolidationCursorV1,
    ConsolidationPayloadV1,
    ConsolidationRunClaimV1,
    ConsolidationRunV1,
    ConsolidationV1,
    ConsolidatorExperienceBindingV1,
    consolidation_run_ref,
)
from atlas_production.modules.learner.public import (
    LearnerExperienceCursorV1,
    LearnerExperienceReader,
    LearnerExperienceV1,
)

SessionFactory = Callable[[], Session]
_RETRY_DELAY = timedelta(seconds=30)
_PAYLOAD_FIELDS = frozenset(ConsolidationPayloadV1.model_fields)


class ConsolidatorConflict(RuntimeError):
    """Fixed source, payload, cursor, or exact replay conflicts."""


class ConsolidatorClaimLost(ConsolidatorConflict):
    """The supplied claim is stale, expired, taken over, or no longer live."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _require_safe_identity(value: str, field_name: str, maximum: int) -> None:
    if not value or len(value) > maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum} characters")


def _require_lease_seconds(lease_seconds: int) -> None:
    if lease_seconds < 1 or lease_seconds > 3600:
        raise ValueError("consolidation lease seconds must be between 1 and 3600")


def _bindings_from_experiences(
    experiences: list[LearnerExperienceV1],
) -> list[ConsolidatorExperienceBindingV1]:
    if len(experiences) != 10:
        raise ConsolidatorConflict("consolidation_exact_ten_required")
    bindings = [
        ConsolidatorExperienceBindingV1(
            experience_ref=experience.payload.source.experience_ref,
            experience_digest=experience.experience_digest,
            scan_sequence=experience.scan_sequence,
        )
        for experience in experiences
    ]
    sequences = [binding.scan_sequence for binding in bindings]
    if sequences != sorted(sequences) or len(set(sequences)) != 10:
        raise ConsolidatorConflict("consolidation_source_order_invalid")
    if len({binding.experience_ref for binding in bindings}) != 10:
        raise ConsolidatorConflict("consolidation_source_identity_invalid")
    return bindings


def _bindings_from_row(
    row: AtlasConsolidationRunRow,
) -> list[ConsolidatorExperienceBindingV1]:
    if not (
        len(row.source_experience_refs)
        == len(row.source_experience_digests)
        == len(row.source_scan_sequences)
        == 10
    ):
        raise ConsolidatorConflict("persisted consolidation source is invalid")
    try:
        return [
            ConsolidatorExperienceBindingV1(
                experience_ref=experience_ref,
                experience_digest=experience_digest,
                scan_sequence=scan_sequence,
            )
            for experience_ref, experience_digest, scan_sequence in zip(
                row.source_experience_refs,
                row.source_experience_digests,
                row.source_scan_sequences,
                strict=True,
            )
        ]
    except (ValidationError, ValueError, TypeError) as exc:
        raise ConsolidatorConflict("persisted consolidation source is invalid") from exc


def _payload_from_row(row: AtlasConsolidationRunRow) -> ConsolidationPayloadV1:
    if row.result_payload is None:
        raise ConsolidatorConflict("completed consolidation lacks result payload")
    try:
        return ConsolidationPayloadV1.model_validate(row.result_payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ConsolidatorConflict("persisted consolidation payload is invalid") from exc


def _run_from_row(row: AtlasConsolidationRunRow) -> ConsolidationRunV1:
    try:
        run = ConsolidationRunV1(
            consolidation_ref=row.consolidation_ref,
            source_bindings=_bindings_from_row(row),
            status=row.status,
            attempt=row.attempt,
            fence=row.fence,
            pinned_route_id=row.pinned_route_id,
            pinned_route_revision=row.pinned_route_revision,
            pinned_runtime_policy_revision=row.pinned_runtime_policy_revision,
            model_invocation_refs=list(row.model_invocation_refs),
            failure_code=row.failure_code,
            next_attempt_at=row.next_attempt_at,
            result_digest=row.result_digest,
            result_scan_sequence=row.scan_sequence,
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise ConsolidatorConflict("persisted consolidation run is invalid") from exc
    if row.status == "completed":
        payload = _payload_from_row(row)
        expected = hashlib.sha256(_canonical(payload.model_dump(mode="json"))).hexdigest()
        if payload.source_bindings != run.source_bindings or row.result_digest != expected:
            raise ConsolidatorConflict("consolidation result does not bind persisted run")
    return run


def _consolidation_from_row(row: AtlasConsolidationRunRow) -> ConsolidationV1:
    run = _run_from_row(row)
    if (
        run.status != "completed"
        or run.result_digest is None
        or run.result_scan_sequence is None
        or run.completed_at is None
    ):
        raise ConsolidatorConflict("consolidation row is not completed")
    return ConsolidationV1(
        consolidation_ref=run.consolidation_ref,
        payload=_payload_from_row(row),
        digest=run.result_digest,
        scan_sequence=run.result_scan_sequence,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


def _claim_from_row(row: AtlasConsolidationRunRow) -> ConsolidationRunClaimV1:
    if row.claim_token is None or row.lease_expires_at is None:
        raise ConsolidatorConflict("consolidation row does not carry a live claim")
    return ConsolidationRunClaimV1(
        consolidation_ref=row.consolidation_ref,
        source_bindings=_bindings_from_row(row),
        attempt=row.attempt,
        fence=row.fence,
        claim_token=row.claim_token,
        lease_expires_at=row.lease_expires_at,
        pinned_route_id=row.pinned_route_id,
        pinned_route_revision=row.pinned_route_revision,
        pinned_runtime_policy_revision=row.pinned_runtime_policy_revision,
    )


def _claim_matches(
    row: AtlasConsolidationRunRow, claim: ConsolidationRunClaimV1
) -> bool:
    return (
        row.consolidation_ref == claim.consolidation_ref
        and _bindings_from_row(row) == claim.source_bindings
        and row.attempt == claim.attempt
        and row.fence == claim.fence
        and row.claim_token == claim.claim_token
    )


def _lock_live_claim(
    session: Session, claim: ConsolidationRunClaimV1, observed_at: datetime
) -> AtlasConsolidationRunRow:
    row = session.scalar(
        select(AtlasConsolidationRunRow)
        .where(AtlasConsolidationRunRow.consolidation_ref == claim.consolidation_ref)
        .with_for_update()
    )
    if (
        row is None
        or not _claim_matches(row, claim)
        or row.status != "consolidating"
        or row.lease_expires_at is None
        or row.lease_expires_at <= observed_at
        or row.lease_expires_at != claim.lease_expires_at
        or row.pinned_route_id != claim.pinned_route_id
        or row.pinned_route_revision != claim.pinned_route_revision
        or row.pinned_runtime_policy_revision != claim.pinned_runtime_policy_revision
    ):
        raise ConsolidatorClaimLost("consolidation claim is no longer live")
    return row


class PostgresConsolidatorOwner:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def reserve_next(
        self, reader: LearnerExperienceReader, observed_at: datetime
    ) -> ConsolidationRunV1 | None:
        with self._session_factory() as session, session.begin():
            session.execute(
                pg_insert(AtlasConsolidatorCheckpointRow)
                .values(
                    checkpoint_key="global",
                    last_scan_sequence=None,
                    last_experience_ref=None,
                    updated_at=observed_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[AtlasConsolidatorCheckpointRow.checkpoint_key]
                )
            )
            checkpoint = session.scalar(
                select(AtlasConsolidatorCheckpointRow)
                .where(AtlasConsolidatorCheckpointRow.checkpoint_key == "global")
                .with_for_update()
            )
            if checkpoint is None:
                raise ConsolidatorConflict("consolidation checkpoint unavailable")
            cursor = (
                None
                if checkpoint.last_scan_sequence is None
                else LearnerExperienceCursorV1(
                    scan_sequence=checkpoint.last_scan_sequence,
                    experience_ref=checkpoint.last_experience_ref,
                )
            )
            experiences = reader.list_experiences_after(cursor, 10)
            if len(experiences) < 10:
                return None
            bindings = _bindings_from_experiences(experiences)
            if cursor is not None and (
                bindings[0].scan_sequence,
                bindings[0].experience_ref,
            ) <= (cursor.scan_sequence, cursor.experience_ref):
                raise ConsolidatorConflict("learner reader did not advance after cursor")
            ref = consolidation_run_ref(source_bindings=bindings)
            session.execute(
                pg_insert(AtlasConsolidationRunRow)
                .values(
                    consolidation_ref=ref,
                    schema_version=SCHEMA_VERSION,
                    prompt_revision=CONSOLIDATOR_PROMPT_REVISION,
                    source_experience_refs=[item.experience_ref for item in bindings],
                    source_experience_digests=[item.experience_digest for item in bindings],
                    source_scan_sequences=[item.scan_sequence for item in bindings],
                    status="pending",
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
                    result_payload=None,
                    result_digest=None,
                    scan_sequence=None,
                    created_at=observed_at,
                    updated_at=observed_at,
                    completed_at=None,
                )
                .on_conflict_do_nothing(
                    index_elements=[AtlasConsolidationRunRow.consolidation_ref]
                )
            )
            row = session.scalar(
                select(AtlasConsolidationRunRow)
                .where(AtlasConsolidationRunRow.consolidation_ref == ref)
                .with_for_update()
            )
            if row is None or _bindings_from_row(row) != bindings:
                raise ConsolidatorConflict("consolidation reservation replay conflicts")
            checkpoint.last_scan_sequence = bindings[-1].scan_sequence
            checkpoint.last_experience_ref = bindings[-1].experience_ref
            checkpoint.updated_at = observed_at
            session.flush()
            return _run_from_row(row)

    def claim_next(
        self, worker_id: str, observed_at: datetime, lease_seconds: int = 300
    ) -> ConsolidationRunClaimV1 | None:
        _require_safe_identity(worker_id, "worker_id", 200)
        _require_lease_seconds(lease_seconds)
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AtlasConsolidationRunRow)
                .where(
                    or_(
                        AtlasConsolidationRunRow.status == "pending",
                        (
                            (AtlasConsolidationRunRow.status == "retryable_failed")
                            & (AtlasConsolidationRunRow.next_attempt_at <= observed_at)
                        ),
                        (
                            (AtlasConsolidationRunRow.status == "consolidating")
                            & (AtlasConsolidationRunRow.lease_expires_at <= observed_at)
                        ),
                    )
                )
                .order_by(
                    AtlasConsolidationRunRow.source_scan_sequences[1],
                    AtlasConsolidationRunRow.consolidation_ref,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.status = "consolidating"
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
        claim: ConsolidationRunClaimV1,
        route_id: str,
        route_revision: int,
        runtime_policy_revision: int,
        observed_at: datetime,
    ) -> ConsolidationRunClaimV1:
        _require_safe_identity(route_id, "route_id", 200)
        if route_revision < 1 or runtime_policy_revision < 1:
            raise ValueError("consolidation route revisions must be positive")
        with self._session_factory() as session, session.begin():
            row = _lock_live_claim(session, claim, observed_at)
            current = (
                row.pinned_route_id,
                row.pinned_route_revision,
                row.pinned_runtime_policy_revision,
            )
            requested = (route_id, route_revision, runtime_policy_revision)
            if current == (None, None, None):
                row.pinned_route_id, row.pinned_route_revision, row.pinned_runtime_policy_revision = requested
                row.updated_at = observed_at
                session.flush()
            elif current != requested:
                raise ConsolidatorConflict("model_route_revision_conflict")
            return _claim_from_row(row)

    def renew_claim(
        self,
        claim: ConsolidationRunClaimV1,
        observed_at: datetime,
        lease_seconds: int = 300,
    ) -> ConsolidationRunClaimV1:
        _require_lease_seconds(lease_seconds)
        with self._session_factory() as session, session.begin():
            row = _lock_live_claim(session, claim, observed_at)
            row.lease_expires_at = observed_at + timedelta(seconds=lease_seconds)
            row.updated_at = observed_at
            session.flush()
            return _claim_from_row(row)

    def complete(
        self,
        claim: ConsolidationRunClaimV1,
        experiences: list[ConsolidatedExperienceV1],
        model_invocation_refs: list[str],
        observed_at: datetime,
    ) -> ConsolidationV1:
        if not model_invocation_refs or len(set(model_invocation_refs)) != len(
            model_invocation_refs
        ):
            raise ConsolidatorConflict("consolidation_invocation_provenance_invalid")
        payload = ConsolidationPayloadV1(
            source_bindings=claim.source_bindings,
            experiences=experiences,
        )
        raw_payload = payload.model_dump(mode="json")
        try:
            validated_payload = validate_typed_payload(
                raw_payload,
                family="consolidation_v1",
                allowed_fields=_PAYLOAD_FIELDS,
                max_bytes=GENERAL_METADATA_MAX_BYTES,
            )
        except PersistedPayloadPolicyError as exc:
            raise ConsolidatorConflict("consolidation_payload_invalid") from exc
        digest = hashlib.sha256(_canonical(validated_payload)).hexdigest()
        with self._session_factory() as session, session.begin():
            row = _lock_live_claim(session, claim, observed_at)
            if any(
                item not in {
                    binding.experience_ref for binding in claim.source_bindings
                }
                for generalized in experiences
                for item in (
                    *generalized.supporting_experience_refs,
                    *generalized.counterexample_experience_refs,
                )
            ):
                raise ConsolidatorConflict("consolidation_payload_invalid")
            scan_sequence = session.scalar(
                select(CONSOLIDATION_SCAN_SEQUENCE.next_value())
            )
            if scan_sequence is None:
                raise ConsolidatorConflict("consolidation sequence unavailable")
            row.status = "completed"
            row.worker_id = None
            row.claim_token = None
            row.lease_expires_at = None
            row.next_attempt_at = None
            row.model_invocation_refs = list(model_invocation_refs)
            row.failure_code = None
            row.result_payload = validated_payload
            row.result_digest = digest
            row.scan_sequence = scan_sequence
            row.updated_at = observed_at
            row.completed_at = observed_at
            session.flush()
            return _consolidation_from_row(row)

    def fail(
        self,
        claim: ConsolidationRunClaimV1,
        failure_code: str,
        retryable: bool,
        observed_at: datetime,
    ) -> ConsolidationRunV1:
        _require_safe_identity(failure_code, "failure_code", 100)
        with self._session_factory() as session, session.begin():
            row = _lock_live_claim(session, claim, observed_at)
            row.status = "retryable_failed" if retryable else "failed"
            row.worker_id = None
            row.claim_token = None
            row.lease_expires_at = None
            row.next_attempt_at = observed_at + _RETRY_DELAY if retryable else None
            row.pinned_route_id = None if retryable else row.pinned_route_id
            row.pinned_route_revision = None if retryable else row.pinned_route_revision
            row.pinned_runtime_policy_revision = (
                None if retryable else row.pinned_runtime_policy_revision
            )
            row.model_invocation_refs = []
            row.failure_code = failure_code
            row.updated_at = observed_at
            session.flush()
            return _run_from_row(row)

    def read_run(self, consolidation_ref: str) -> ConsolidationRunV1 | None:
        _require_safe_identity(consolidation_ref, "consolidation_ref", 300)
        with self._session_factory() as session:
            row = session.get(AtlasConsolidationRunRow, consolidation_ref)
            return None if row is None else _run_from_row(row)

    def read_consolidation(self, consolidation_ref: str) -> ConsolidationV1 | None:
        _require_safe_identity(consolidation_ref, "consolidation_ref", 300)
        with self._session_factory() as session:
            row = session.scalar(
                select(AtlasConsolidationRunRow).where(
                    AtlasConsolidationRunRow.consolidation_ref == consolidation_ref,
                    AtlasConsolidationRunRow.status == "completed",
                )
            )
            return None if row is None else _consolidation_from_row(row)

    def list_consolidations_after(
        self, cursor: ConsolidationCursorV1 | None, limit: int
    ) -> list[ConsolidationV1]:
        if limit < 1 or limit > 100:
            raise ValueError("consolidation list limit must be between 1 and 100")
        with self._session_factory() as session, session.begin():
            statement = (
                select(AtlasConsolidationRunRow)
                .where(AtlasConsolidationRunRow.status == "completed")
                .order_by(
                    AtlasConsolidationRunRow.scan_sequence,
                    AtlasConsolidationRunRow.consolidation_ref,
                )
                .limit(limit)
                .with_for_update(key_share=True)
            )
            if cursor is not None:
                statement = statement.where(
                    tuple_(
                        AtlasConsolidationRunRow.scan_sequence,
                        AtlasConsolidationRunRow.consolidation_ref,
                    )
                    > (cursor.scan_sequence, cursor.consolidation_ref)
                )
            return [_consolidation_from_row(row) for row in session.scalars(statement)]

    def read_source_experiences(
        self,
        claim: ConsolidationRunClaimV1,
        reader: LearnerExperienceReader,
    ) -> list[LearnerExperienceV1]:
        experiences: list[LearnerExperienceV1] = []
        for binding in claim.source_bindings:
            experience = reader.read_experience(binding.experience_ref)
            if experience is None:
                raise ConsolidatorConflict("consolidation_source_unavailable")
            if (
                experience.experience_digest != binding.experience_digest
                or experience.scan_sequence != binding.scan_sequence
            ):
                raise ConsolidatorConflict("consolidation_source_integrity_conflict")
            experiences.append(experience)
        if _bindings_from_experiences(experiences) != claim.source_bindings:
            raise ConsolidatorConflict("consolidation_source_integrity_conflict")
        return experiences


__all__ = [
    "ConsolidatorClaimLost",
    "ConsolidatorConflict",
    "PostgresConsolidatorOwner",
]
