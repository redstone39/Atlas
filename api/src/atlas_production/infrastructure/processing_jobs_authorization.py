from __future__ import annotations

from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.processing_pipeline.job_records import (
    DocumentJobRequestAuthorityProjection,
)

from atlas_production.rbac import (
    direct_team_role,
    effective_document_scope,
    is_system_admin,
    resolve_access,
    team_role_covers,
)


class RbacProcessingJobsAuthorization:
    def can_read(
        self,
        projection: DocumentJobRequestAuthorityProjection,
        actor: UserRecord,
    ) -> bool:
        allowed = effective_document_scope(
            projection.authorization_state,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="read_derived",
        )
        return any(ref in allowed for ref in projection.tag_refs)

    def can_control(
        self,
        projection: DocumentJobRequestAuthorityProjection,
        actor: UserRecord,
    ) -> bool:
        document = projection.document
        state = projection.authorization_state
        if document.uploader_actor_id == actor.actor_id:
            return True
        if is_system_admin(state, actor.actor_type, actor.actor_id):
            return True
        if document.scope_type == "team":
            return team_role_covers(
                direct_team_role(
                    state,
                    actor.actor_type,
                    actor.actor_id,
                    document.scope_id or "",
                ),
                "admin",
            )
        if document.scope_type == "project" and document.scope_id:
            return resolve_access(
                state,
                actor_type=actor.actor_type,
                actor_id=actor.actor_id,
                project_id=document.scope_id,
                action="permission_manage",
                persist=False,
            ).allowed
        return False
