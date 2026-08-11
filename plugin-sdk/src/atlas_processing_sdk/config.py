from __future__ import annotations

from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from .admin import AdminClientError


def config_path() -> Path:
    override = os.getenv("ATLAS_PLUGIN_CONFIG")
    return Path(override).expanduser() if override else Path.home() / ".config" / "atlas" / "plugin.json"


def load_config() -> dict[str, str]:
    path = config_path()
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {key: item for key, item in value.items() if key in {"base_url", "token"} and isinstance(item, str)} if isinstance(value, dict) else {}


def login_and_store(base_url: str, email: str, password: str) -> dict[str, str]:
    if not base_url.startswith(("http://", "https://")):
        raise AdminClientError("base URL must use http or https")
    if not email or not password:
        raise AdminClientError("email and password are required")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/v1/auth/sessions",
        data=json.dumps({"identifier": email, "password": password}, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            cookie = SimpleCookie()
            cookie.load(response.headers.get("Set-Cookie", ""))
    except urllib.error.HTTPError as exc:
        raise AdminClientError(f"Atlas login returned HTTP {exc.code}") from exc
    morsel = cookie.get("atlas_session")
    if morsel is None or not morsel.value:
        raise AdminClientError("Atlas login did not issue a session token")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps({"base_url": base_url.rstrip("/"), "token": morsel.value}, separators=(",", ":")))
    path.chmod(0o600)
    return {"status": "authenticated", "config": str(path)}
