from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Callable

from pydantic import ValidationError
from sqlalchemy import or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.learner import (
    AtlasLearnerRunRow,
    LEARNER_EXPERIENCE_SCAN_SEQUENCE,
)
from atlas_production.infrastructure.persistence.payload_policy import (
    GENERAL_METADATA_MAX_BYTES,
    PersistedPayloadPolicyError,
    validate_typed_payload,
)
from atlas_production.modules.learner.public import (
    LEARNER_PROMPT_REVISION,
    SCHEMA_VERSION,
    LearnerExperienceCursorV1,
    LearnerExperiencePayloadV1,
    LearnerExperienceV1,
    LearnerRunClaimV1,
    LearnerRunV1,
    LearnerSourceIdentityV1,
    RegisterLearnerCaseV1,
    learner_case_digest,
    learner_experience_ref,
    learner_run_ref,
)

SessionFactory = Callable[[], Session]
_RETRY_DELAY = timedelta(seconds=30)
_PAYLOAD_FIELDS = frozenset(LearnerExperiencePayloadV1.model_fields)


class LearnerConflict(RuntimeError):
    """Immutable Learner identity, payload, or exact replay conflicts."""


class LearnerClaimLost(LearnerConflict):
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
        raise ValueError("learner lease seconds must be between 1 and 3600")


def _source_from_command(command: RegisterLearnerCaseV1) -> LearnerSourceIdentityV1:
    case_digest = learner_case_digest(
        review_ref=command.review_ref,
        review_digest=command.review_digest,
        case_ordinal=command.case.case_ordinal,
        case=command.case,
    )
    run_ref = learner_run_ref(
        review_ref=command.review_ref,
        review_digest=command.review_digest,
        case_ordinal=command.case.case_ordinal,
        case_digest=case_digest,
    )
    return LearnerSourceIdentityV1(
        run_ref=run_ref,
        experience_ref=learner_experience_ref(run_ref=run_ref),
        review_ref=command.review_ref,
        review_digest=command.review_digest,
        snapshot_digest=command.snapshot_digest,
        case_ordinal=command.case.case_ordinal,
        case_digest=case_digest,
        case_title=command.case.title,
        involved_turn_ids=command.case.involved_turn_ids,
        primary_assistant_turn_id=command.case.primary_assistant_turn_id,
    )


def _source_from_row(row: AtlasLearnerRunRow) -> LearnerSourceIdentityV1:
    return LearnerSourceIdentityV1(
        run_ref=row.run_ref,
        experience_ref=row.experience_ref,
        schema_version=row.schema_version,
        learner_prompt_revision=row.learner_prompt_revision,
        review_ref=row.review_ref,
        review_digest=row.review_digest,
        snapshot_digest=row.snapshot_digest,
        case_ordinal=row.case_ordinal,
        case_digest=row.case_digest,
        case_title=row.case_title,
        involved_turn_ids=list(row.involved_turn_ids),
        primary_assistant_turn_id=row.primary_assistant_turn_id,
    )


def _payload_from_row(row: AtlasLearnerRunRow) -> LearnerExperiencePayloadV1:
    if row.experience_payload is None:
        raise LearnerConflict("completed learner row lacks Experience payload")
    try:
        return LearnerExperiencePayloadV1.model_validate(row.experience_payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise LearnerConflict("persisted learner Experience is invalid") from exc


def _run_from_row(row: AtlasLearnerRunRow) -> LearnerRunV1:
    try:
        run = LearnerRunV1(
            source=_source_from_row(row),
            status=row.status,
            attempt=row.attempt,
            fence=row.fence,
            pinned_route_id=row.pinned_route_id,
            pinned_route_revision=row.pinned_route_revision,
            pinned_runtime_policy_revision=row.pinned_runtime_policy_revision,
            model_invocation_refs=list(row.model_invocation_refs),
            failure_code=row.failure_code,
            next_attempt_at=row.next_attempt_at,
            experience_digest=row.experience_digest,
            scan_sequence=row.scan_sequence,
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise LearnerConflict("persisted learner run is invalid") from exc
    if row.status == "completed":
        payload = _payload_from_row(row)
        expected = hashlib.sha256(_canonical(payload.model_dump(mode="json"))).hexdigest()
        if payload.source != run.source or row.experience_digest != expected:
            raise LearnerConflict("learner Experience does not bind persisted run")
    return run


def _experience_from_row(row: AtlasLearnerRunRow) -> LearnerExperienceV1:
    run = _run_from_row(row)
    if (
        run.status != "completed"
        or run.experience_digest is None
        or run.scan_sequence is None
        or run.completed_at is None
    ):
        raise LearnerConflict("learner row is not completed")
    return LearnerExperienceV1(
        payload=_payload_from_row(row),
        experience_digest=run.experience_digest,
        scan_sequence=run.scan_sequence,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


def _claim_from_row(row: AtlasLearnerRunRow) -> LearnerRunClaimV1:
    if row.claim_token is None or row.lease_expires_at is None:
        raise LearnerConflict("learner row does not carry a live claim")
    return LearnerRunClaimV1(
        run_ref=row.run_ref,
        experience_ref=row.experience_ref,
        attempt=row.attempt,
        fence=row.fence,
        claim_token=row.claim_token,
        lease_expires_at=row.lease_expires_at,
        pinned_route_id=row.pinned_route_id,
        pinned_route_revision=row.pinned_route_revision,
        pinned_runtime_policy_revision=row.pinned_runtime_policy_revision,
    )


def _claim_identity_matches(row: AtlasLearnerRunRow, claim: LearnerRunClaimV1) -> bool:
    return (
        row.run_ref == claim.run_ref
        and row.experience_ref == claim.experience_ref
        and row.attempt == claim.attempt
        and row.fence == claim.fence
        and row.claim_token == claim.claim_token
    )


def _lock_live_claim(
    session: Session, claim: LearnerRunClaimV1, observed_at: datetime
) -> AtlasLearnerRunRow:
    row = session.scalar(
        select(AtlasLearnerRunRow)
        .where(AtlasLearnerRunRow.run_ref == claim.run_ref)
        .with_for_update()
    )
    if (
        row is None
        or not _claim_identity_matches(row, claim)
        or row.status != "learning"
        or row.lease_expires_at is None
        or row.lease_expires_at <= observed_at
        or row.lease_expires_at != claim.lease_expires_at
        or row.pinned_route_id != claim.pinned_route_id
        or row.pinned_route_revision != claim.pinned_route_revision
        or row.pinned_runtime_policy_revision != claim.pinned_runtime_policy_revision
    ):
        raise LearnerClaimLost("learner claim is no longer live")
    return row


class PostgresLearnerOwner:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def register_case(self, command: RegisterLearnerCaseV1) -> LearnerRunV1:
        source = _source_from_command(command)
        with self._session_factory() as session, session.begin():
            inserted_ref = session.scalar(
                pg_insert(AtlasLearnerRunRow)
                .values(
                    **source.model_dump(mode="python"),
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
                    experience_payload=None,
                    experience_digest=None,
                    scan_sequence=None,
                    completed_at=None,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        AtlasLearnerRunRow.review_ref,
                        AtlasLearnerRunRow.review_digest,
                        AtlasLearnerRunRow.case_ordinal,
                        AtlasLearnerRunRow.case_digest,
                        AtlasLearnerRunRow.schema_version,
                        AtlasLearnerRunRow.learner_prompt_revision,
                    ]
                )
                .returning(AtlasLearnerRunRow.run_ref)
            )
            if inserted_ref is not None and inserted_ref != source.run_ref:
                raise LearnerConflict("learner registration returned another identity")
            row = session.scalar(
                select(AtlasLearnerRunRow)
                .where(AtlasLearnerRunRow.run_ref == source.run_ref)
                .with_for_update()
            )
            if row is None or _source_from_row(row) != source:
                raise LearnerConflict("learner case replay source projection conflicts")
            return _run_from_row(row)

    def claim_next(
        self, worker_id: str, observed_at: datetime, lease_seconds: int = 300
    ) -> LearnerRunClaimV1 | None:
        _require_safe_identity(worker_id, "worker_id", 200)
        _require_lease_seconds(lease_seconds)
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AtlasLearnerRunRow)
                .where(
                    or_(
                        AtlasLearnerRunRow.status == "pending",
                        (
                            (AtlasLearnerRunRow.status == "retryable_failed")
                            & (AtlasLearnerRunRow.next_attempt_at <= observed_at)
                        ),
                        (
                            (AtlasLearnerRunRow.status == "learning")
                            & (AtlasLearnerRunRow.lease_expires_at <= observed_at)
                        ),
                    )
                )
                .order_by(AtlasLearnerRunRow.created_at, AtlasLearnerRunRow.run_ref)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.status = "learning"
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
        claim: LearnerRunClaimV1,
        route_id: str,
        route_revision: int,
        runtime_policy_revision: int,
        observed_at: datetime,
    ) -> LearnerRunClaimV1:
        _require_safe_identity(route_id, "route_id", 200)
        if route_revision < 1 or runtime_policy_revision < 1:
            raise ValueError("learner route revisions must be positive")
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
                raise LearnerConflict("model_route_revision_conflict")
            return _claim_from_row(row)

    def renew_claim(
        self,
        claim: LearnerRunClaimV1,
        observed_at: datetime,
        lease_seconds: int = 300,
    ) -> LearnerRunClaimV1:
        _require_lease_seconds(lease_seconds)
        with self._session_factory() as session, session.begin():
            row = _lock_live_claim(session, claim, observed_at)
            row.lease_expires_at = observed_at + timedelta(seconds=lease_seconds)
            row.updated_at = observed_at
            session.flush()
            return _claim_from_row(row)

    def complete(
        self,
        claim: LearnerRunClaimV1,
        payload: LearnerExperiencePayloadV1,
        observed_at: datetime,
    ) -> LearnerExperienceV1:
        with self._session_factory() as session, session.begin():
            return self.complete_in_session(session, claim, payload, observed_at)

    def complete_in_session(
        self,
        session: Session,
        claim: LearnerRunClaimV1,
        payload: LearnerExperiencePayloadV1,
        observed_at: datetime,
    ) -> LearnerExperienceV1:
        payload_data = payload.model_dump(mode="json")
        try:
            validated = validate_typed_payload(
                payload_data,
                family="learner_experience_v1",
                allowed_fields=_PAYLOAD_FIELDS,
                max_bytes=GENERAL_METADATA_MAX_BYTES,
            )
        except PersistedPayloadPolicyError as exc:
            raise LearnerConflict("learner_experience_payload_invalid") from exc
        digest = hashlib.sha256(_canonical(validated)).hexdigest()
        row = _lock_live_claim(session, claim, observed_at)
        source = _source_from_row(row)
        if payload.source != source:
            raise LearnerConflict("learner_source_identity_mismatch")
        route = (
            payload.route_id,
            payload.route_revision,
            payload.runtime_policy_revision,
        )
        if route != (
            row.pinned_route_id,
            row.pinned_route_revision,
            row.pinned_runtime_policy_revision,
        ):
            raise LearnerConflict("model_route_revision_conflict")
        if list(payload.model_invocation_refs) != list(
            dict.fromkeys(payload.model_invocation_refs)
        ):
            raise LearnerConflict("learner_experience_payload_invalid")
        row.status = "completed"
        row.worker_id = None
        row.claim_token = None
        row.lease_expires_at = None
        row.next_attempt_at = None
        row.model_invocation_refs = list(payload.model_invocation_refs)
        row.failure_code = None
        row.experience_payload = validated
        row.experience_digest = digest
        row.scan_sequence = session.scalar(
            select(LEARNER_EXPERIENCE_SCAN_SEQUENCE.next_value())
        )
        row.completed_at = observed_at
        row.updated_at = observed_at
        session.flush()
        return _experience_from_row(row)

    def fail(
        self,
        claim: LearnerRunClaimV1,
        failure_code: str,
        retryable: bool,
        observed_at: datetime,
    ) -> LearnerRunV1:
        _require_safe_identity(failure_code, "failure_code", 100)
        target = "retryable_failed" if retryable else "failed"
        with self._session_factory() as session, session.begin():
            row = _lock_live_claim(session, claim, observed_at)
            row.status = target
            row.worker_id = None
            row.claim_token = None
            row.lease_expires_at = None
            row.next_attempt_at = observed_at + _RETRY_DELAY if retryable else None
            row.failure_code = failure_code
            row.updated_at = observed_at
            session.flush()
            return _run_from_row(row)

    def read_run(self, run_ref: str) -> LearnerRunV1 | None:
        _require_safe_identity(run_ref, "run_ref", 300)
        with self._session_factory() as session, session.begin():
            row = session.get(AtlasLearnerRunRow, run_ref)
            return _run_from_row(row) if row is not None else None

    def read_experience(self, experience_ref: str) -> LearnerExperienceV1 | None:
        _require_safe_identity(experience_ref, "experience_ref", 300)
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AtlasLearnerRunRow).where(
                    AtlasLearnerRunRow.experience_ref == experience_ref,
                    AtlasLearnerRunRow.status == "completed",
                )
            )
            return _experience_from_row(row) if row is not None else None

    def list_experiences_after(
        self, cursor: LearnerExperienceCursorV1 | None, limit: int
    ) -> list[LearnerExperienceV1]:
        if limit < 1 or limit > 100:
            raise ValueError("learner experience scan limit must be between 1 and 100")
        statement = select(AtlasLearnerRunRow).where(AtlasLearnerRunRow.status == "completed")
        if cursor is not None:
            statement = statement.where(
                tuple_(AtlasLearnerRunRow.scan_sequence, AtlasLearnerRunRow.experience_ref)
                > (cursor.scan_sequence, cursor.experience_ref)
            )
        statement = statement.order_by(
            AtlasLearnerRunRow.scan_sequence, AtlasLearnerRunRow.experience_ref
        ).limit(limit)
        with self._session_factory() as session, session.begin():
            session.execute(text("LOCK TABLE atlas_learner_runs IN SHARE MODE"))
            rows = session.scalars(statement).all()
            return [_experience_from_row(row) for row in rows]


__all__ = ["LearnerClaimLost", "LearnerConflict", "PostgresLearnerOwner"]
