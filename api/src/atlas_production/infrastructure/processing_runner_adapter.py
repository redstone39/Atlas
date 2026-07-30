from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from io import BytesIO
import os
from typing import Any

import httpx

class ProcessingRunnerError(RuntimeError):
    def __init__(self, safe_code: str, *, safe_type: str | None = None) -> None:
        self.safe_code = safe_code
        self.safe_type = (
            safe_type
            if isinstance(safe_type, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", safe_type)
            else None
        )
        super().__init__(safe_code)


class HttpProcessingPluginRunner:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        artifact = payload.get("artifact")
        package = payload.get("package")
        if not isinstance(artifact, bytes) or (
            package is not None and not isinstance(package, bytes)
        ):
            raise ProcessingRunnerError("invalid_artifact_envelope")
        metadata = {
            key: value
            for key, value in payload.items()
            if key not in {"artifact", "package"}
        }
        try:
            metadata_json = json.dumps(
                metadata,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ProcessingRunnerError("invalid_artifact_envelope") from exc
        files: list[tuple[str, tuple[str, str | bytes, str]]] = [
            (
                "metadata",
                ("metadata.json", metadata_json, "application/json"),
            ),
            (
                "artifact",
                ("artifact.bin", artifact, "application/octet-stream"),
            ),
        ]
        if package is not None:
            files.append(
                (
                    "package",
                    ("plugin.atlas-plugin", package, "application/octet-stream"),
                )
            )
        try:
            response = httpx.post(
                f"{self.base_url}/internal/v1/invocations",
                files=files,
                timeout=float(payload.get("timeout_seconds", 60)) + 5,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            raise ProcessingRunnerError("plugin_runner_unavailable") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            error = result.get("error", {}) if isinstance(result, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            safe_type = error.get("type") if isinstance(error, dict) else None
            raise ProcessingRunnerError(
                code or "plugin_execution_failed", safe_type=safe_type
            )
        return result


class LocalBuiltinProcessingPluginRunner:
    """Explicit local/test adapter; Production Compose always uses the runner service."""

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            artifact = payload["artifact"]
            if not isinstance(artifact, bytes):
                raise TypeError("artifact must be bytes")
            entrypoint = payload["entrypoint"]
            if entrypoint.endswith(":PypdfPlugin"):
                from pypdf import PdfReader
                drafts, assets = [], {}
                request = payload["request"]
                reader = PdfReader(BytesIO(artifact))
                if reader.is_encrypted and not reader.decrypt(""):
                    raise ValueError("encrypted PDF requires a password")
                pages = reader.pages
                unit_start = int(request["unit_start"])
                unit_end = int(request["unit_end"])
                if unit_start < 1 or unit_end < unit_start or unit_end > len(pages):
                    raise ValueError("invalid parser page range")
                for page_number in range(unit_start, unit_end + 1):
                    page = pages[page_number - 1]
                    text = (page.extract_text() or "").strip()
                    ref = None
                    if text:
                        ref = f"runner-text:{len(assets) + 1}"
                        assets[ref] = base64.b64encode(text.encode()).decode()
                    drafts.append({
                        "source_region_identity": f"page:{page_number}",
                        "region_kind": "page",
                        "content_kind_hint": "text" if ref else "unknown",
                        "element_kind_hint": "page", "parent_region_identity": None,
                        "locator_draft": {"selector_kind": "page_region", "page_number": page_number},
                        "normalized_text_ref": ref, "structured_content_ref": None,
                        "native_artifact_ref": None, "quality_flag_refs": [],
                    })
                return {"ok": True, "drafts": drafts, "assets": assets}
            if entrypoint.endswith(":InlineTextPlugin"):
                request = payload["request"]
                if request["unit_start"] != 1 or request["unit_end"] != 1:
                    raise ValueError("inline parser supports one batch")
                text = artifact.decode("utf-8").strip()
                if not text:
                    return {"ok": True, "drafts": [], "assets": {}}
                drafts, assets = [], {}
                paragraphs = [
                    value.strip()
                    for value in re.split(r"\n\s*\n", text)
                    if value.strip()
                ]
                for ordinal, paragraph in enumerate(paragraphs, start=1):
                    ref = f"runner-text:{ordinal}"
                    assets[ref] = base64.b64encode(paragraph.encode()).decode()
                    drafts.append({
                        "source_region_identity": f"paragraph:{ordinal}", "region_kind": "paragraph",
                        "content_kind_hint": "text", "element_kind_hint": "paragraph",
                        "parent_region_identity": None,
                        "locator_draft": {"selector_kind": "normalized_text_span", "ordinal": ordinal},
                        "normalized_text_ref": ref, "structured_content_ref": None,
                        "native_artifact_ref": None, "quality_flag_refs": [],
                    })
                return {"ok": True, "drafts": drafts, "assets": assets}
            if entrypoint.endswith(":GenericTextPlugin"):
                request = payload["request"]
                ref = request.get("normalized_text_ref")
                if not ref or ref not in payload.get("input_assets", {}):
                    return {"ok": True, "drafts": [], "assets": {}}
                return {"ok": True, "drafts": [{
                    "source_region_ids": [request["region_id"]],
                    "channel_id": "generic_text", "output_contract_version": "eir-draft-v1",
                    "candidate_payload_ref": ref,
                    "content_kind_hint": request["content_kind_hint"],
                    "element_kind_hint": request.get("element_kind_hint"),
                    "structured_content_ref": None, "native_artifact_ref": None,
                    "table_grid": None, "cell_bboxes": None, "table_asset_refs": [],
                    "figure_asset_refs": [], "content_rendition_ref": None,
                    "preview_region": None, "quality_flag_refs": [],
                }], "assets": {}}
            if entrypoint.endswith(":DoclingLayoutPlugin"):
                # The local/test adapter intentionally has no Docling runtime.
                # Returning no precision candidates exercises the supported
                # whole-page/base-text degradation without inventing geometry.
                return {"ok": True, "drafts": [], "assets": {}}
        except Exception as exc:
            raise ProcessingRunnerError("plugin_execution_failed") from exc
        raise ProcessingRunnerError("external_plugin_requires_runner_service")


def default_processing_runner():
    base_url = os.getenv("ATLAS_PLUGIN_RUNNER_URL")
    return HttpProcessingPluginRunner(base_url) if base_url else LocalBuiltinProcessingPluginRunner()
