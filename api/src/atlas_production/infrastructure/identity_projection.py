from __future__ import annotations

from atlas_production.modules.identity_access.public import (
    ActorContext,
)
from atlas_production.modules.identity_access.records import (
    UserRecord,
)
from atlas_production.shared.correlation import current_correlation_id


def actor_context(user: UserRecord) -> ActorContext:
    return ActorContext(
        actor_id=user.actor_id,
        actor_type="user",
        issuer="atlas-local-dev",
        display_name=user.display_name,
        groups=[],
        correlation_id=current_correlation_id(),
    )
