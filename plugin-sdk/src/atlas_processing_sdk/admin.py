"""Authenticated Atlas System Admin API client used by the CLI."""

from __future__ import annotations

import json
import mimetypes
import uuid
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AdminClientError(RuntimeError):
    pass


@dataclass
class AdminClient:
    base_url: str
    token: str
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise AdminClientError("base URL must use http or https")
        if not self.token:
            raise AdminClientError("an admin token is required")

    def request(self, method: str, path: str, *, body: Any = None, idempotency_key: str | None = None, expected_revision: int | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if method != "GET":
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        if expected_revision is not None:
            headers["If-Match"] = str(expected_revision)
        request = urllib.request.Request(self.base_url.rstrip("/") + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            safe = exc.read(64 * 1024).decode("utf-8", "replace")
            raise AdminClientError(f"Atlas API returned HTTP {exc.code}: {safe}") from exc
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise AdminClientError(f"Atlas API request failed: {exc}") from exc

    def upload(self, package: Path, *, idempotency_key: str | None = None) -> Any:
        if package.suffix != ".atlas-plugin" or not package.is_file():
            raise AdminClientError("upload accepts exactly one existing .atlas-plugin file")
        boundary = "atlas-plugin-" + uuid.uuid4().hex
        content = package.read_bytes()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"package\"; filename=\"{package.name}\"\r\n"
            f"Content-Type: {mimetypes.guess_type(package.name)[0] or 'application/zip'}\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        headers = {
            "Authorization": f"Bearer {self.token}", "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Idempotency-Key": idempotency_key or str(uuid.uuid4()),
        }
        request = urllib.request.Request(self.base_url.rstrip("/") + "/api/v1/admin/processing-plugins/packages", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise AdminClientError(f"Atlas API returned HTTP {exc.code}: {exc.read(64 * 1024).decode('utf-8', 'replace')}") from exc


def add_query(path: str, values: dict[str, Any]) -> str:
    filtered = {key: value for key, value in values.items() if value is not None}
    return path + ("?" + urllib.parse.urlencode(filtered) if filtered else "")
