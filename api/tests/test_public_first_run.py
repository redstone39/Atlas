from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas_production.infrastructure.deployment_secret_init import (
    SecretProjectionPaths,
    materialize_deployment_secrets,
)
from atlas_production.modules.identity_access.api_models import (
    ActorContext,
    FirstAdminClaimRequest,
    SessionState,
)
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.identity_access.service import IdentityAccessService


class FirstAdminRepository:
    def __init__(self) -> None:
        self.claims: list[dict[str, str]] = []

    def list_users(self) -> list[UserRecord]:
        return []

    def claim_first_admin(self, **claim: str) -> tuple[UserRecord, str]:
        self.claims.append(claim)
        return (
            UserRecord(
                actor_id="actor-public-synthetic-owner",
                display_name=claim["display_name"],
                email=claim["email"],
                system_role="admin",
                password_digest=claim["password_digest"],
            ),
            "session-public-synthetic-owner",
        )

    def session_state(self, user: UserRecord) -> SessionState:
        return SessionState(
            authenticated=True,
            actor=ActorContext(
                actor_id=user.actor_id,
                actor_type="user",
                issuer="local",
                display_name=user.display_name,
                groups=[],
                correlation_id="public-synthetic-correlation",
            ),
            available_projects=[],
            system_role="admin",
        )


def test_first_admin_contract_rejects_caller_selected_actor_id() -> None:
    with pytest.raises(ValidationError) as error:
        FirstAdminClaimRequest.model_validate(
            {
                "display_name": "Public Synthetic Admin",
                "email": "admin@example.test",
                "password": "public-synthetic-password",
                "actor_id": "caller-selected-admin",
            }
        )

    assert any(
        item["loc"] == ("actor_id",) and item["type"] == "extra_forbidden"
        for item in error.value.errors()
    )


def test_first_admin_service_returns_the_owner_allocated_actor_id() -> None:
    repository = FirstAdminRepository()
    service = IdentityAccessService(repository, scope_grants=object())  # type: ignore[arg-type]

    outcome = service.claim_first_admin(
        FirstAdminClaimRequest(
            display_name=" Public Synthetic Admin ",
            email=" ADMIN@EXAMPLE.TEST ",
            password="public-synthetic-password",
        )
    )

    assert outcome.session.actor is not None
    assert outcome.session.actor.actor_id == "actor-public-synthetic-owner"
    assert outcome.raw_session_token == "session-public-synthetic-owner"
    assert repository.claims[0]["email"] == "admin@example.test"
    assert repository.claims[0]["password_digest"] != "public-synthetic-password"


def test_generated_deployment_secrets_are_private_and_stable(tmp_path: Path) -> None:
    paths = SecretProjectionPaths(
        credential_directory=tmp_path / "credentials",
        notes_transport_directory=tmp_path / "notes-transport",
        notes_ticket_directory=tmp_path / "notes-ticket",
    )
    owner = os.getuid()
    group = os.getgid()

    first = materialize_deployment_secrets(
        environment={},
        paths=paths,
        root_uid=owner,
        root_gid=group,
        node_uid=owner,
        node_gid=group,
    )
    projected = [
        paths.credential_directory / "active_key",
        paths.credential_directory / "key_id",
        paths.notes_transport_directory / "secret",
        paths.notes_ticket_directory / "secret",
    ]
    contents = [path.read_text() for path in projected]
    second = materialize_deployment_secrets(
        environment={},
        paths=paths,
        root_uid=owner,
        root_gid=group,
        node_uid=owner,
        node_gid=group,
    )

    assert first == {"credential": True, "notes_transport": True, "notes_ticket": True}
    assert second == {"credential": False, "notes_transport": False, "notes_ticket": False}
    assert [path.read_text() for path in projected] == contents
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in projected)
    assert contents[2] != contents[3]
