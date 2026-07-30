from __future__ import annotations

from sqlalchemy import delete, select

import pytest

from atlas_production.infrastructure.persistence.audit_events import (
    AtlasAuditEventRow,
    _audit_metadata_payload,
)
from atlas_production.infrastructure.persistence.turn_execution import (
    AtlasTurnAnswerBehaviorRevisionRow,
)
from atlas_production.infrastructure.postgres_owner.answer_behavior import (
    PostgresAnswerBehaviorOwner,
)
from atlas_production.infrastructure.postgres_runtime import PostgresRuntime
from atlas_production.modules.turn_execution.public import (
    AnswerBehaviorError,
    AnswerBehaviorUpdateRequest,
)


PREFIX = "answer-behavior-integration"


@pytest.fixture(autouse=True)
def clean_rows(postgres_runtime: PostgresRuntime):
    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(
            delete(AtlasAuditEventRow).where(
                AtlasAuditEventRow.target_ref.like("answer-behavior:%")
            )
        )
        session.execute(delete(AtlasTurnAnswerBehaviorRevisionRow))
    yield
    with postgres_runtime.session_factory() as session, session.begin():
        session.execute(
            delete(AtlasAuditEventRow).where(
                AtlasAuditEventRow.target_ref.like("answer-behavior:%")
            )
        )
        session.execute(delete(AtlasTurnAnswerBehaviorRevisionRow))


def _payload(
    guidance: str | None,
    revision: int,
    key: str,
) -> AnswerBehaviorUpdateRequest:
    return AnswerBehaviorUpdateRequest(
        custom_guidance=guidance,
        expected_revision=revision,
        idempotency_key=f"{PREFIX}-{key}",
    )


def test_answer_behavior_append_only_revision_idempotency_clear_and_safe_audit(
    postgres_runtime: PostgresRuntime,
) -> None:
    owner = PostgresAnswerBehaviorOwner(postgres_runtime.session_factory)
    assert owner.current().revision == 0
    assert owner.status().custom_guidance is None

    first_payload = _payload("  Prefer concise answers.  ", 0, "first")
    first = owner.update(actor_id="admin-1", payload=first_payload)
    replay = owner.update(actor_id="admin-1", payload=first_payload)
    assert replay == first
    assert first.revision == 1
    assert first.custom_guidance == "Prefer concise answers."
    assert first.updated_by == "admin-1"
    assert owner.read_exact(
        revision=first.revision,
        guidance_digest=first.guidance_digest,
    ).custom_guidance == first.custom_guidance

    with pytest.raises(AnswerBehaviorError) as replay_conflict:
        owner.update(
            actor_id="admin-1",
            payload=_payload("Changed", 0, "first"),
        )
    assert replay_conflict.value.error_code == "idempotency_conflict"

    with pytest.raises(AnswerBehaviorError) as revision_conflict:
        owner.update(
            actor_id="admin-1",
            payload=_payload("Changed", 0, "stale"),
        )
    assert revision_conflict.value.error_code == "revision_conflict"

    cleared = owner.update(
        actor_id="admin-1",
        payload=_payload(" \n ", 1, "clear"),
    )
    assert cleared.revision == 2
    assert cleared.custom_guidance is None
    assert cleared.guidance_digest is not None
    assert owner.read_exact(
        revision=2,
        guidance_digest=cleared.guidance_digest,
    ).custom_guidance is None

    bounded = owner.update(
        actor_id="admin-1",
        payload=_payload("x" * 2000, 2, "bounded"),
    )
    assert len(bounded.custom_guidance or "") == 2000

    with postgres_runtime.session_factory() as session:
        revisions = session.scalars(
            select(AtlasTurnAnswerBehaviorRevisionRow).order_by(
                AtlasTurnAnswerBehaviorRevisionRow.revision
            )
        ).all()
        audits = session.scalars(
            select(AtlasAuditEventRow)
            .where(AtlasAuditEventRow.target_ref.like("answer-behavior:%"))
            .order_by(AtlasAuditEventRow.target_ref)
        ).all()
    assert [item.revision for item in revisions] == [1, 2, 3]
    assert len(audits) == 3
    for audit in audits:
        validated = _audit_metadata_payload(audit.event_metadata)
        assert set(validated) == {
            "guidance_character_count",
            "guidance_digest",
            "request_id",
            "revision",
            "status",
        }
        assert len(validated["guidance_digest"]) == 64
        assert validated["guidance_character_count"] in {0, 2000, 23}
        assert "custom_guidance" not in audit.event_metadata
        assert "Prefer concise answers." not in str(audit.event_metadata)
