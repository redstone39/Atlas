from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings


@dataclass(frozen=True, slots=True)
class McpTransportConfig:
    mode: str
    public_url: str | None
    transport_security: TransportSecuritySettings

    @classmethod
    def from_environment(cls) -> "McpTransportConfig":
        raw = os.environ.get("ATLAS_MCP_PUBLIC_URL", "").strip()
        if not raw:
            return cls(
                mode="localhost_only",
                public_url=None,
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=True,
                    allowed_hosts=["127.0.0.1:*", "localhost:*", "127.0.0.1", "localhost"],
                    allowed_origins=[
                        "http://127.0.0.1:*",
                        "http://localhost:*",
                        "https://127.0.0.1:*",
                        "https://localhost:*",
                    ],
                ),
            )
        parsed = urlsplit(raw)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/", "/mcp"}
        ):
            raise RuntimeError(
                "ATLAS_MCP_PUBLIC_URL must be an http(s) origin or exact /mcp URL"
            )
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return cls(
            mode="public_url",
            public_url=origin + "/mcp",
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[parsed.netloc],
                allowed_origins=[origin],
            ),
        )


__all__ = ["McpTransportConfig"]
