from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from time import sleep

from sqlalchemy import text
from sqlalchemy.orm import Session


def advisory_lock_key(owner_key: str) -> int:
    """Return the stable signed PostgreSQL advisory key for a namespaced owner."""

    if ":" not in owner_key or owner_key.startswith(":") or owner_key.endswith(":"):
        raise ValueError("owner advisory key must include a domain namespace")
    return int.from_bytes(
        hashlib.sha256(owner_key.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )


def acquire_owner_locks(
    session: Session,
    *,
    domain_keys: Iterable[str] = (),
    identity_keys: Iterable[str] = (),
) -> None:
    """Acquire domain-control locks first, then sorted identity locks."""

    ordered_domain = tuple(sorted(set(domain_keys)))
    ordered_identity = tuple(
        key for key in sorted(set(identity_keys)) if key not in ordered_domain
    )
    for owner_key in (*ordered_domain, *ordered_identity):
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": advisory_lock_key(owner_key)},
        )


def acquire_shared_owner_locks(
    session: Session,
    *,
    identity_keys: Iterable[str],
) -> None:
    """Acquire sorted transaction-scoped shared locks for read/data-plane fences."""

    for owner_key in sorted(set(identity_keys)):
        session.execute(
            text("SELECT pg_advisory_xact_lock_shared(:lock_key)"),
            {"lock_key": advisory_lock_key(owner_key)},
        )


def acquire_mixed_owner_locks(
    session: Session,
    *,
    shared_domain_keys: Iterable[str] = (),
    exclusive_domain_keys: Iterable[str] = (),
    shared_identity_keys: Iterable[str] = (),
    exclusive_identity_keys: Iterable[str] = (),
) -> None:
    """Acquire one canonical identity plan with an explicit mode per key."""

    shared_domain = set(shared_domain_keys)
    exclusive_domain = set(exclusive_domain_keys)
    shared_identity = set(shared_identity_keys)
    exclusive_identity = set(exclusive_identity_keys)
    shared = shared_domain | shared_identity
    exclusive = exclusive_domain | exclusive_identity
    overlap = shared & exclusive
    if overlap:
        raise ValueError(
            "mixed advisory lock plan cannot assign two modes to one identity"
        )
    ordered_domain = sorted(shared_domain | exclusive_domain)
    ordered_identity = sorted(
        (shared_identity | exclusive_identity) - set(ordered_domain)
    )
    for owner_key in (*ordered_domain, *ordered_identity):
        function = (
            "pg_advisory_xact_lock"
            if owner_key in exclusive
            else "pg_advisory_xact_lock_shared"
        )
        session.execute(
            text(f"SELECT {function}(:lock_key)"),
            {"lock_key": advisory_lock_key(owner_key)},
        )


@contextmanager
def hold_owner_operation_lock(
    session_factory: Callable[[], Session],
    *,
    owner_key: str,
) -> Iterator[None]:
    """Serialize detached work without a transaction or blocked pool waiter."""

    lock_key = advisory_lock_key(owner_key)
    with session_factory() as discovery:
        bind = discovery.get_bind()
    while True:
        with bind.connect() as connection:
            acquired = bool(
                connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": lock_key},
                )
            )
            connection.commit()
            if not acquired:
                sleep(0.01)
                continue
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": lock_key},
                )
                connection.commit()
            return


__all__ = [
    "acquire_mixed_owner_locks",
    "acquire_owner_locks",
    "acquire_shared_owner_locks",
    "advisory_lock_key",
    "hold_owner_operation_lock",
]
