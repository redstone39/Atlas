"""Atomic PostgreSQL authority for immutable prompt-skill revisions and catalogs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_production.infrastructure.persistence.prompt_skills import (
    AtlasPromptSkillCatalogRevisionRow,
    AtlasPromptSkillControlRow,
    AtlasPromptSkillIdempotencyRow,
    AtlasPromptSkillRevisionRow,
)
from atlas_production.infrastructure.postgres_audit_adapter import build_audit_event
from atlas_production.infrastructure.postgres_locks import acquire_owner_locks
from atlas_production.infrastructure.postgres_owner.audit import AuditEventWriter
from atlas_production.modules.prompt_skills.public import (
    PromptSkillCatalogRefV1,
    PromptSkillCatalogV1,
    PromptSkillCategory,
    PromptSkillControlV1,
    PromptSkillError,
    PromptSkillInstructionsV1,
    PromptSkillLifecycleRequest,
    PromptSkillListV1,
    PromptSkillMutationOutcomeV1,
    PromptSkillRefV1,
    PromptSkillRevisionV1,
    PromptSkillSelectorCandidateV1,
    PromptSkillSummaryV1,
)
from atlas_production.modules.prompt_skills.service import parse_skill_file


SessionFactory = Callable[[], Session]
_EMPTY_CATALOG_DIGEST = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _request_digest(value: object) -> str:
    return _canonical_digest(value)


def _not_found() -> PromptSkillError:
    return PromptSkillError(
        "prompt_skill_not_found", "prompt_skills.revision_was_not_found", 404
    )


def _integrity_error() -> PromptSkillError:
    return PromptSkillError(
        "prompt_skill_integrity_error",
        "prompt_skills.stored_content_is_invalid",
        503,
    )


def _ref(row: AtlasPromptSkillRevisionRow) -> PromptSkillRefV1:
    return PromptSkillRefV1(
        category=cast(PromptSkillCategory, row.category),
        name=row.name,
        revision=row.revision,
        content_digest=row.content_digest,
    )


def _validate_revision(row: AtlasPromptSkillRevisionRow) -> None:
    try:
        parsed = parse_skill_file(
            "SKILL.md",
            row.source.encode("utf-8"),
            persisted_source=True,
        )
    except PromptSkillError as error:
        raise _integrity_error() from error
    if (
        parsed.source != row.source
        or parsed.content_digest != row.content_digest
        or parsed.name != row.name
        or parsed.description != row.description
        or parsed.license != row.license
        or parsed.compatibility != row.compatibility
        or parsed.metadata != row.skill_metadata
        or parsed.instructions != row.instructions
    ):
        raise _integrity_error()


def _revision(
    row: AtlasPromptSkillRevisionRow,
    *,
    enabled_revision: int | None,
    include_body: bool,
) -> PromptSkillRevisionV1:
    _validate_revision(row)
    return PromptSkillRevisionV1(
        ref=_ref(row),
        description=row.description,
        license=row.license,
        compatibility=row.compatibility,
        metadata=dict(row.skill_metadata),
        created_by=row.created_by,
        created_at=row.created_at,
        enabled=row.revision == enabled_revision,
        source=row.source if include_body else None,
        instructions=row.instructions if include_body else None,
    )


def _control(row: AtlasPromptSkillControlRow) -> PromptSkillControlV1:
    return PromptSkillControlV1(
        category=cast(PromptSkillCategory, row.category),
        name=row.name,
        head_revision=row.head_revision,
        enabled_revision=row.enabled_revision,
        control_revision=row.control_revision,
    )


class PostgresPromptSkillOwner:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _control_row(
        session: Session, category: PromptSkillCategory, name: str
    ) -> AtlasPromptSkillControlRow | None:
        return session.get(AtlasPromptSkillControlRow, (category, name))

    @staticmethod
    def _revision_row(
        session: Session, category: PromptSkillCategory, name: str, revision: int
    ) -> AtlasPromptSkillRevisionRow | None:
        return session.get(AtlasPromptSkillRevisionRow, (category, name, revision))

    @classmethod
    def _summary(
        cls, session: Session, control: AtlasPromptSkillControlRow
    ) -> PromptSkillSummaryV1:
        rows = list(
            session.scalars(
                select(AtlasPromptSkillRevisionRow)
                .where(
                    AtlasPromptSkillRevisionRow.category == control.category,
                    AtlasPromptSkillRevisionRow.name == control.name,
                )
                .order_by(AtlasPromptSkillRevisionRow.revision.desc())
            )
        )
        if not rows or rows[0].revision != control.head_revision:
            raise _integrity_error()
        revisions = [
            _revision(
                row,
                enabled_revision=control.enabled_revision,
                include_body=False,
            )
            for row in rows
        ]
        return PromptSkillSummaryV1(
            control=_control(control), head=revisions[0], revisions=revisions
        )

    @staticmethod
    def _replay(
        session: Session,
        *,
        idempotency_key: str,
        operation: str,
        request_digest: str,
    ) -> PromptSkillMutationOutcomeV1 | None:
        row = session.get(AtlasPromptSkillIdempotencyRow, idempotency_key)
        if row is None:
            return None
        if row.operation != operation or row.request_digest != request_digest:
            raise PromptSkillError(
                "idempotency_conflict",
                "prompt_skills.idempotency_key_was_reused",
                409,
            )
        return PromptSkillMutationOutcomeV1.model_validate(
            row.response_payload
        ).model_copy(update={"replayed": True})

    @staticmethod
    def _store_replay(
        session: Session,
        *,
        idempotency_key: str,
        operation: str,
        request_digest: str,
        outcome: PromptSkillMutationOutcomeV1,
        now: datetime,
    ) -> None:
        session.add(
            AtlasPromptSkillIdempotencyRow(
                idempotency_key=idempotency_key,
                operation=operation,
                request_digest=request_digest,
                response_payload=outcome.model_dump(mode="json"),
                status_code=200,
                created_at=now,
            )
        )

    def list_skills(self, category: PromptSkillCategory) -> PromptSkillListV1:
        with self._session_factory() as session:
            controls = list(
                session.scalars(
                    select(AtlasPromptSkillControlRow)
                    .where(AtlasPromptSkillControlRow.category == category)
                    .order_by(AtlasPromptSkillControlRow.name)
                )
            )
            return PromptSkillListV1(
                items=[self._summary(session, control) for control in controls]
            )

    def get_revision_by_identity(
        self,
        *,
        category: PromptSkillCategory,
        name: str,
        revision: int,
        include_body: bool,
    ) -> PromptSkillRevisionV1:
        with self._session_factory() as session:
            row = self._revision_row(session, category, name, revision)
            control = self._control_row(session, category, name)
            if row is None or control is None:
                raise _not_found()
            return _revision(
                row,
                enabled_revision=control.enabled_revision,
                include_body=include_body,
            )

    def read_instructions(self, ref: PromptSkillRefV1) -> PromptSkillInstructionsV1:
        with self._session_factory() as session:
            row = self._revision_row(session, ref.category, ref.name, ref.revision)
            if row is None or row.content_digest != ref.content_digest:
                raise _integrity_error()
            _validate_revision(row)
            return PromptSkillInstructionsV1(
                name=row.name,
                revision=row.revision,
                content_digest=row.content_digest,
                instructions=row.instructions,
            )

    def current_catalog(
        self, category: PromptSkillCategory
    ) -> PromptSkillCatalogRefV1:
        with self._session_factory() as session:
            row = session.scalar(
                select(AtlasPromptSkillCatalogRevisionRow)
                .where(AtlasPromptSkillCatalogRevisionRow.category == category)
                .order_by(AtlasPromptSkillCatalogRevisionRow.catalog_revision.desc())
                .limit(1)
            )
            if row is None:
                raise _integrity_error()
            self._validate_catalog(row)
            return PromptSkillCatalogRefV1(
                category=category,
                catalog_revision=row.catalog_revision,
                catalog_digest=row.catalog_digest,
            )

    @staticmethod
    def _validate_catalog(row: AtlasPromptSkillCatalogRevisionRow) -> None:
        if _canonical_digest(row.refs) != row.catalog_digest:
            raise _integrity_error()
        if any(item.get("category") != row.category for item in row.refs):
            raise _integrity_error()
        names = [item.get("name") for item in row.refs]
        if names != sorted(names) or len(names) != len(set(names)):
            raise _integrity_error()

    def read_catalog(self, ref: PromptSkillCatalogRefV1) -> PromptSkillCatalogV1:
        with self._session_factory() as session:
            row = session.get(
                AtlasPromptSkillCatalogRevisionRow,
                (ref.category, ref.catalog_revision),
            )
            if row is None or row.catalog_digest != ref.catalog_digest:
                raise _integrity_error()
            self._validate_catalog(row)
            candidates: list[PromptSkillSelectorCandidateV1] = []
            for item in row.refs:
                skill_ref = PromptSkillRefV1.model_validate(item)
                revision = self._revision_row(
                    session, skill_ref.category, skill_ref.name, skill_ref.revision
                )
                if revision is None or revision.content_digest != skill_ref.content_digest:
                    raise _integrity_error()
                _validate_revision(revision)
                candidates.append(
                    PromptSkillSelectorCandidateV1(
                        selection_id=(
                            f"{skill_ref.category}:{skill_ref.name}:"
                            f"{skill_ref.revision}:{skill_ref.content_digest}"
                        ),
                        name=skill_ref.name,
                        description=revision.description,
                        ref=skill_ref,
                    )
                )
            return PromptSkillCatalogV1(ref=ref, skills=candidates)

    def upload_parsed(
        self,
        *,
        actor_id: str,
        category: PromptSkillCategory,
        path_name: str,
        source: str,
        description: str,
        license: str | None,
        compatibility: str | None,
        metadata: dict[str, str],
        instructions: str,
        content_digest: str,
        expected_head_revision: int,
        idempotency_key: str,
    ) -> PromptSkillMutationOutcomeV1:
        operation = "upload"
        digest = _request_digest(
            {
                "operation": operation,
                "category": category,
                "name": path_name,
                "content_digest": content_digest,
                "expected_head_revision": expected_head_revision,
            }
        )
        session = self._session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    domain_keys=(
                        f"prompt-skills:idempotency:{idempotency_key}",
                        f"prompt-skills:{category}:{path_name}",
                    ),
                )
                replay = self._replay(
                    session,
                    idempotency_key=idempotency_key,
                    operation=operation,
                    request_digest=digest,
                )
                if replay is not None:
                    session.rollback()
                    return replay
                control = self._control_row(session, category, path_name)
                current_head = 0 if control is None else control.head_revision
                if current_head != expected_head_revision:
                    raise PromptSkillError(
                        "revision_conflict",
                        "prompt_skills.head_revision_changed",
                        412,
                    )
                next_revision = current_head + 1
                now = _now()
                row = AtlasPromptSkillRevisionRow(
                    category=category,
                    name=path_name,
                    revision=next_revision,
                    source=source,
                    description=description,
                    license=license,
                    compatibility=compatibility,
                    skill_metadata=metadata,
                    instructions=instructions,
                    content_digest=content_digest,
                    created_by=actor_id,
                    created_at=now,
                )
                session.add(row)
                session.flush()
                if control is None:
                    control = AtlasPromptSkillControlRow(
                        category=category,
                        name=path_name,
                        head_revision=next_revision,
                        enabled_revision=None,
                        control_revision=1,
                        updated_at=now,
                    )
                    session.add(control)
                else:
                    control.head_revision = next_revision
                    control.control_revision += 1
                    control.updated_at = now
                session.flush()
                outcome = PromptSkillMutationOutcomeV1(
                    skill=self._summary(session, control),
                    revision=_revision(
                        row,
                        enabled_revision=control.enabled_revision,
                        include_body=False,
                    ),
                )
                audit = build_audit_event(
                    event_type="prompt_skill_revision_uploaded",
                    actor_id=actor_id,
                    target_ref=f"prompt-skill:{category}:{path_name}:{next_revision}",
                    project_id=None,
                    message_code="prompt_skills.revision_was_uploaded",
                    metadata={
                        "category_id": category,
                        "logical_identity": path_name,
                        "revision": next_revision,
                        "digest": content_digest,
                        "status": "disabled",
                        "request_id": idempotency_key,
                    },
                )
                AuditEventWriter(session).append(audit)
                self._store_replay(
                    session,
                    idempotency_key=idempotency_key,
                    operation=operation,
                    request_digest=digest,
                    outcome=outcome,
                    now=now,
                )
                session.commit()
                return outcome
            except Exception:
                session.rollback()
                raise

    def mutate_enabled(
        self,
        *,
        actor_id: str,
        ref: PromptSkillRefV1,
        enable: bool,
        request: PromptSkillLifecycleRequest,
    ) -> PromptSkillMutationOutcomeV1:
        operation = "enable" if enable else "disable"
        digest = _request_digest(
            {
                "operation": operation,
                "ref": ref.model_dump(mode="json"),
                "expected_control_revision": request.expected_control_revision,
            }
        )
        session = self._session_factory()
        with session:
            try:
                acquire_owner_locks(
                    session,
                    domain_keys=(
                        f"prompt-skills:idempotency:{request.idempotency_key}",
                        f"prompt-skills:{ref.category}:catalog",
                        f"prompt-skills:{ref.category}:{ref.name}",
                    ),
                )
                replay = self._replay(
                    session,
                    idempotency_key=request.idempotency_key,
                    operation=operation,
                    request_digest=digest,
                )
                if replay is not None:
                    session.rollback()
                    return replay
                control = self._control_row(session, ref.category, ref.name)
                row = self._revision_row(
                    session, ref.category, ref.name, ref.revision
                )
                if control is None or row is None:
                    raise _not_found()
                if row.content_digest != ref.content_digest:
                    raise _integrity_error()
                if control.control_revision != request.expected_control_revision:
                    raise PromptSkillError(
                        "revision_conflict",
                        "prompt_skills.control_revision_changed",
                        412,
                    )
                if enable and control.enabled_revision == ref.revision:
                    raise PromptSkillError(
                        "prompt_skill_state_conflict",
                        "prompt_skills.revision_is_already_enabled",
                        409,
                    )
                if not enable and control.enabled_revision != ref.revision:
                    raise PromptSkillError(
                        "prompt_skill_state_conflict",
                        "prompt_skills.revision_is_not_enabled",
                        409,
                    )
                now = _now()
                control.enabled_revision = ref.revision if enable else None
                control.control_revision += 1
                control.updated_at = now
                session.flush()
                controls = list(
                    session.scalars(
                        select(AtlasPromptSkillControlRow)
                        .where(
                            AtlasPromptSkillControlRow.category == ref.category,
                            AtlasPromptSkillControlRow.enabled_revision.is_not(None),
                        )
                        .order_by(AtlasPromptSkillControlRow.name)
                    )
                )
                refs: list[dict[str, object]] = []
                for item in controls:
                    revision = self._revision_row(
                        session,
                        cast(PromptSkillCategory, item.category),
                        item.name,
                        cast(int, item.enabled_revision),
                    )
                    if revision is None:
                        raise _integrity_error()
                    refs.append(_ref(revision).model_dump(mode="json"))
                latest_catalog = session.scalar(
                    select(AtlasPromptSkillCatalogRevisionRow)
                    .where(
                        AtlasPromptSkillCatalogRevisionRow.category == ref.category
                    )
                    .order_by(
                        AtlasPromptSkillCatalogRevisionRow.catalog_revision.desc()
                    )
                    .limit(1)
                )
                if latest_catalog is None:
                    raise _integrity_error()
                next_catalog_revision = latest_catalog.catalog_revision + 1
                catalog_digest = _canonical_digest(refs)
                session.add(
                    AtlasPromptSkillCatalogRevisionRow(
                        category=ref.category,
                        catalog_revision=next_catalog_revision,
                        catalog_digest=catalog_digest,
                        refs=refs,
                        created_by=actor_id,
                        created_at=now,
                    )
                )
                session.flush()
                outcome = PromptSkillMutationOutcomeV1(
                    skill=self._summary(session, control), revision=None
                )
                audit = build_audit_event(
                    event_type=f"prompt_skill_revision_{operation}d",
                    actor_id=actor_id,
                    target_ref=(
                        f"prompt-skill:{ref.category}:{ref.name}:{ref.revision}"
                    ),
                    project_id=None,
                    message_code=f"prompt_skills.revision_was_{operation}d",
                    metadata={
                        "category_id": ref.category,
                        "logical_identity": ref.name,
                        "revision": ref.revision,
                        "digest": ref.content_digest,
                        "trace_ref": (
                            f"prompt-skill-catalog:{ref.category}:"
                            f"{next_catalog_revision}:{catalog_digest}"
                        ),
                        "request_id": request.idempotency_key,
                    },
                )
                AuditEventWriter(session).append(audit)
                self._store_replay(
                    session,
                    idempotency_key=request.idempotency_key,
                    operation=operation,
                    request_digest=digest,
                    outcome=outcome,
                    now=now,
                )
                session.commit()
                return outcome
            except Exception:
                session.rollback()
                raise


__all__ = ["PostgresPromptSkillOwner", "_EMPTY_CATALOG_DIGEST"]
