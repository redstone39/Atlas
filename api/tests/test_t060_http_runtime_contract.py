from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from atlas_production.app import create_app
from atlas_production.infrastructure.composition import ApiComposition
from atlas_production.modules.identity_access.contracts import LoginOutcome
from atlas_production.modules.identity_access.public import SessionState
from atlas_production.modules.identity_access.records import UserRecord


_FIELDS = tuple(ApiComposition.__dataclass_fields__)


def _composition(**overrides: object) -> ApiComposition:
    """Build one route-local composition without a full-system state fake."""
    values = {name: object() for name in _FIELDS}
    values.update(overrides)
    return ApiComposition(**values)


class _Principal:
    actor = UserRecord("user-1", "Atlas User", "user@example.test", "user", None)

    def current_user(self, _token: str | None):
        return self.actor


class _Identity:
    session = SessionState(
        authenticated=True,
        actor={
            "actor_id": "user-1",
            "actor_type": "user",
            "issuer": "atlas-local",
            "display_name": "Atlas User",
            "groups": [],
            "correlation_id": "login-correlation",
        },
        available_projects=[],
        system_role="user",
    )

    def login(self, _payload):
        return LoginOutcome(self.session, "opaque-session-token")

    def session_for_token(self, _token):
        return self.session

    def logout(self, _token):
        return None


class _ProtectedOriginals:
    def build(self, **facts):
        return SimpleNamespace(
            request=SimpleNamespace(document_id=facts["document_id"]),
            method=facts["method"],
            filename="source.pdf",
            if_match=facts["if_match"],
            if_none_match=facts["if_none_match"],
            if_range=facts["if_range"],
            range_header=facts["range_header"],
        )


class _OriginalBytes:
    def open_original(self, _request, *, method, range_header, **_conditionals):
        headers = {
            "Accept-Ranges": "bytes",
            "ETag": '"sha256:document"',
            "Content-Type": "application/pdf",
        }
        if method == "HEAD":
            headers["Content-Length"] = "6"
            return SimpleNamespace(status_code=200, headers=headers, body=())
        if range_header == "bytes=1-3":
            headers.update({"Content-Range": "bytes 1-3/6", "Content-Length": "3"})
            return SimpleNamespace(status_code=206, headers=headers, body=(b"bcd",))
        headers["Content-Length"] = "6"
        return SimpleNamespace(status_code=200, headers=headers, body=(b"abcdef",))


def test_runtime_app_preserves_auth_cookie_and_correlation_header() -> None:
    client = TestClient(
        create_app(
            _composition(
                current_principal=_Principal(),
                identity_access=_Identity(),
            )
        )
    )

    response = client.post(
        "/api/v1/auth/sessions",
        json={"identifier": "user@example.test", "password": "secret"},
    )

    assert response.status_code == 200
    assert "atlas_session=opaque-session-token" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.headers["x-atlas-correlation-id"]


def test_runtime_original_content_preserves_head_range_and_etag() -> None:
    client = TestClient(
        create_app(
            _composition(
                current_principal=_Principal(),
                protected_originals=_ProtectedOriginals(),
                artifact_storage=_OriginalBytes(),
            )
        )
    )

    ranged = client.get(
        "/api/v1/library/documents/document-1/content",
        headers={"Range": "bytes=1-3"},
    )
    headed = client.head("/api/v1/library/documents/document-1/content")

    assert (ranged.status_code, ranged.content) == (206, b"bcd")
    assert ranged.headers["content-range"] == "bytes 1-3/6"
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.headers["etag"] == '"sha256:document"'
    assert headed.status_code == 200
    assert headed.content == b""
    assert headed.headers["content-length"] == "6"
    assert headed.headers["etag"] == '"sha256:document"'


def test_legacy_request_scoped_sse_reconnect_is_removed() -> None:
    client = TestClient(
        create_app(
            _composition(current_principal=_Principal())
        )
    )

    response = client.get(
        "/api/v1/workspace/conversations/conversation-1/turn-requests/request-1/stream",
        headers={"Last-Event-ID": "event-1"},
    )

    assert response.status_code == 404
    assert response.headers["x-atlas-correlation-id"]
