from __future__ import annotations

from dataclasses import dataclass, field
import os

import httpx

from atlas_production.modules.notes.public import (
    BodyRestoreCommandV1,
    BodyRestoreResultV1,
)


@dataclass(frozen=True, slots=True)
class HttpNotesCollaborationClient:
    base_url: str | None
    internal_secret: str | None = field(repr=False)
    public_url: str | None
    ticket_secret_isolated: bool

    @classmethod
    def from_environment(cls) -> "HttpNotesCollaborationClient":
        base_url = os.environ.get("ATLAS_NOTES_COLLABORATION_INTERNAL_URL")
        internal_secret = os.environ.get(
            "ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET"
        )
        ticket_secret = os.environ.get(
            "ATLAS_NOTES_COLLABORATION_TICKET_SECRET"
        )
        return cls(
            base_url=base_url.rstrip("/") if base_url else None,
            internal_secret=internal_secret,
            public_url=os.environ.get("ATLAS_NOTES_COLLABORATION_PUBLIC_URL"),
            ticket_secret_isolated=bool(
                ticket_secret and ticket_secret != internal_secret
            ),
        )

    def _headers(self) -> dict[str, str]:
        if not self.internal_secret:
            raise OSError("Notes collaboration internal secret is unavailable")
        return {"X-Atlas-Notes-Internal-Secret": self.internal_secret}

    def _post(self, path: str, payload: dict[str, object], *, timeout: float = 5.0):
        if not self.base_url:
            raise OSError("Notes collaboration internal URL is unavailable")
        try:
            response = httpx.post(
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            raise OSError("Notes collaboration carrier request failed") from exc

    def invalidate_room(self, note_id: str, epoch: int) -> None:
        self._post(
            "/internal/v1/rooms/invalidate",
            {"note_id": note_id, "collaboration_epoch": epoch},
        )

    def reschedule_settings(self, revision: int) -> None:
        self._post(
            "/internal/v1/settings/reschedule",
            {"settings_revision": revision},
        )

    def restore_body(self, command: BodyRestoreCommandV1) -> BodyRestoreResultV1:
        response = self._post(
            "/internal/v1/restores",
            command.model_dump(mode="json"),
            timeout=30.0,
        )
        try:
            return BodyRestoreResultV1.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise OSError("Notes collaboration restore response is invalid") from exc

    def readiness_available(self) -> bool:
        if (
            not self.base_url
            or not self.internal_secret
            or not self.public_url
            or not self.ticket_secret_isolated
        ):
            return False
        try:
            response = httpx.get(
                f"{self.base_url}/ready",
                headers=self._headers(),
                timeout=2.0,
            )
            return response.status_code == 200
        except (httpx.HTTPError, OSError):
            return False


__all__ = ["HttpNotesCollaborationClient"]
