from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from atlas_production.modules.identity_access.api_models import (
    AgentUserCreateRequest,
    DirectoryConnectionCreateRequest,
    TeamCreateRequest,
)
from atlas_production.modules.identity_access.records import UserRecord
from atlas_production.modules.model_routing.api_models import (
    ModelRouteCreateRequest,
    ProviderConnectionCreateRequest,
)
from atlas_production.modules.notes.public import (
    NoteCategoryCreateRequestV1,
    NoteCreateRequestV1,
)
from atlas_production.modules.processing_pipeline.api_models import ProfileCreateRequest
from atlas_production.modules.project_governance.api_models import ProjectCreateRequest
from atlas_production.routes import document_library


@pytest.mark.parametrize(
    ("request_type", "legacy_field"),
    [
        (ProjectCreateRequest, "project_id"),
        (TeamCreateRequest, "team_id"),
        (AgentUserCreateRequest, "actor_id"),
        (DirectoryConnectionCreateRequest, "connection_id"),
        (ProviderConnectionCreateRequest, "connection_id"),
        (ModelRouteCreateRequest, "route_id"),
        (ProfileCreateRequest, "profile_id"),
        (NoteCreateRequestV1, "note_id"),
        (NoteCategoryCreateRequestV1, "category_id"),
    ],
)
def test_create_contracts_reject_caller_selected_resource_ids(
    request_type: type, legacy_field: str
) -> None:
    with pytest.raises(ValidationError) as error:
        request_type.model_validate({legacy_field: "public-synthetic-caller-id"})

    assert any(
        item["loc"] == (legacy_field,) and item["type"] == "extra_forbidden"
        for item in error.value.errors()
    )


@pytest.mark.anyio
async def test_document_upload_rejects_legacy_id_before_owner_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = UserRecord(
        actor_id="actor-public-synthetic-admin",
        display_name="Public Synthetic Admin",
        email="admin@example.test",
        system_role="admin",
        password_digest="public-synthetic-digest",
    )
    owner_called = False

    async def form():
        return {
            "document_id": "public-synthetic-caller-document",
            "file": object(),
        }

    def forbidden_composition(_request):
        nonlocal owner_called
        owner_called = True
        raise AssertionError("document owner was invoked for an invalid legacy field")

    monkeypatch.setattr(document_library, "current_user", lambda _request: actor)
    monkeypatch.setattr(document_library, "api_composition", forbidden_composition)

    response = await document_library.upload_document_library_file(
        SimpleNamespace(form=form)
    )

    assert response.status_code == 422
    assert owner_called is False
