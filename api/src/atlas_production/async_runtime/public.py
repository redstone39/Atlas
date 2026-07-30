from __future__ import annotations

import os


def best_effort_dispatch() -> None:
    """Reduce queue latency; the durable beat scan remains authoritative."""
    if not os.getenv("ATLAS_PRODUCTION_DATABASE_URL"):
        return
    try:
        from atlas_production.async_runtime.tasks import dispatch_pending_outbox

        dispatch_pending_outbox.apply_async(queue="atlas.dispatch")
    except Exception:
        return
