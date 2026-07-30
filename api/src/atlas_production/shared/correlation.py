"""Server-owned request correlation context.

Correlation identifiers are intentionally generated inside the Atlas process.  No
client header, cookie, path value, actor identifier, or request content is used to
derive them.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from secrets import token_urlsafe
from typing import Iterator


_CORRELATION_ID: ContextVar[str | None] = ContextVar(
    "atlas_server_correlation_id",
    default=None,
)


def new_correlation_id() -> str:
    """Return an opaque, URL-safe identifier with no client-derived material."""

    return f"corr_{token_urlsafe(18)}"


def current_correlation_id() -> str:
    """Return the active request id, or an isolated id outside request handling."""

    return _CORRELATION_ID.get() or new_correlation_id()


@contextmanager
def server_correlation_context() -> Iterator[str]:
    """Bind one newly generated correlation id for the current execution context."""

    correlation_id = new_correlation_id()
    token = _CORRELATION_ID.set(correlation_id)
    try:
        yield correlation_id
    finally:
        _CORRELATION_ID.reset(token)
