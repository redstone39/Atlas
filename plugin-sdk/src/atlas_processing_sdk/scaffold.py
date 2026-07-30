from __future__ import annotations

import json
from pathlib import Path


def _write(path: Path, value: str | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _output_schema(plugin_kind: str) -> dict:
    ref = {"type": "string", "minLength": 1}
    locator = {"type": "object", "minProperties": 1}
    common = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
    }
    preview_region = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "page_number": {"type": "integer", "minimum": 1},
            "region_kind": {"enum": ["paragraph", "table", "figure", "image"]},
            "source_element_id": ref,
            "coordinate_system": {"const": "pdf_crop_box_relative_bottom_left"},
            "rectangles": {
                "type": "array", "minItems": 1,
                "items": {
                    "type": "array", "minItems": 4, "maxItems": 4,
                    "items": {"type": "number"},
                },
            },
            "page_width": {"type": "number", "exclusiveMinimum": 0},
            "page_height": {"type": "number", "exclusiveMinimum": 0},
            "geometry_version": {"const": "docling-page-region-v1"},
        },
        "required": [
            "page_number", "region_kind", "source_element_id", "coordinate_system",
            "rectangles", "page_width", "page_height", "geometry_version",
        ],
    }
    if plugin_kind == "base_parser":
        properties = {
            "source_region_identity": ref,
            "region_kind": {"enum": ["page", "slide", "paragraph", "table", "figure", "image_region"]},
            "content_kind_hint": {"enum": ["text", "table", "figure", "formula", "image", "unknown"]},
            "locator_draft": locator,
            "element_kind_hint": _nullable(ref),
            "parent_region_identity": _nullable(ref),
            "normalized_text_ref": _nullable(ref),
            "structured_content_ref": _nullable(ref),
            "native_artifact_ref": _nullable(ref),
            "quality_flag_refs": {"type": "array", "items": ref},
        }
        return {
            **common,
            "properties": properties,
            "required": list(properties),
        }
    properties = {
        "source_region_ids": {"type": "array", "minItems": 1, "items": ref},
        "channel_id": ref,
        "output_contract_version": {"const": "eir-draft-v1"},
        "candidate_payload_ref": ref,
        "content_kind_hint": {"enum": ["text", "table", "figure", "formula", "image", "unknown"]},
        "element_kind_hint": _nullable(ref),
        "structured_content_ref": _nullable(ref),
        "native_artifact_ref": _nullable(ref),
        "table_grid": _nullable({"type": "object"}),
        "cell_bboxes": _nullable({
            "type": "object",
            "additionalProperties": {
                "type": "array", "minItems": 4, "maxItems": 4,
                "items": {"type": "number"},
            },
        }),
        "table_asset_refs": {"type": "array", "items": ref},
        "figure_asset_refs": {"type": "array", "items": ref},
        "content_rendition_ref": _nullable(ref),
        "preview_region": _nullable(preview_region),
        "quality_flag_refs": {"type": "array", "items": ref},
    }
    return {
        **common,
        "properties": properties,
        "required": list(properties),
        "allOf": [{
            "if": {"properties": {"channel_id": {"const": "table"}}, "required": ["channel_id"]},
            "then": {
                "properties": {
                    "content_kind_hint": {"const": "table"},
                    "element_kind_hint": {"const": "table"},
                    "table_grid": {"type": "object"},
                    "cell_bboxes": {"type": "object", "minProperties": 1},
                }
            },
        }],
    }


def init_project(
    destination: Path,
    plugin_id: str,
    plugin_kind: str = "region_processor",
    *,
    sdk_source: Path | None = None,
) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("destination must be absent or empty")
    destination.mkdir(parents=True, exist_ok=True)
    class_name = "Plugin"
    method = "parse" if plugin_kind == "base_parser" else "process"
    draft = "SourceRegionDraft" if plugin_kind == "base_parser" else "CandidateDraft"
    _write(destination / "manifest.yaml", {
        "accepted_media_types": ["application/pdf"],
        "accepted_region_kinds": [] if plugin_kind == "base_parser" else ["page", "paragraph", "table", "figure"],
        "accepted_element_kind_hints": [] if plugin_kind == "base_parser" else ["page"],
        "accepted_content_kind_hints": ["text", "table", "figure", "formula", "image", "unknown"],
        "produced_channels": ["generic_text"],
        "declared_capabilities": ["pdf_read"],
        "config_schema_ref": "schemas/config.schema.json",
        "entrypoint": f"plugin:{class_name}",
        "kind": plugin_kind,
        "license_expression": "Apache-2.0",
        "network_access": False,
        "output_contract_version": "eir-draft-v1", "plugin_id": plugin_id,
        "plugin_version": "0.1.0",
        "runtime_profile": "atlas-python-v1", "sdk_api_version": 1,
        "timeout_policy_ref": "atlas-default-v1",
        "digest_algorithm": "atlas-plugin-digest-v1",
    })
    _write(destination / "src/plugin.py", f'''from atlas_processing_sdk import {draft}\n\nclass {class_name}:\n    async def {method}(self, request, context):\n        if False:\n            yield None\n''')
    source_block = ""
    if sdk_source is not None:
        source_block = (
            "\n[tool.uv.sources]\n"
            f"atlas-processing-sdk = {{ path = {json.dumps(str(sdk_source))}, editable = false }}\n"
        )
    _write(destination / "pyproject.toml", f'''[project]\nname = "{plugin_id}"\nversion = "0.1.0"\nrequires-python = ">=3.12"\ndependencies = ["atlas-processing-sdk>=0.1,<0.2"]\n{source_block}''')
    _write(destination / "schemas/config.schema.json", {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False})
    _write(destination / "schemas/output.schema.json", _output_schema(plugin_kind))
    common_request = {
        "run_id": "run-smoke", "invocation_id": "invocation-smoke",
        "document_id": "document-smoke", "document_version_id": "version-smoke",
        "artifact_ref": "artifact:smoke", "media_type": "application/pdf",
        "profile_id": "profile-smoke", "profile_revision": 1,
        "policy_snapshot_ref": "policy:smoke", "deadline_at": "2030-01-01T00:00:00+00:00",
        "plugin_config": {},
    }
    if plugin_kind == "base_parser":
        common_request.update({
            "batch_id": "batch-smoke", "unit_start": 1, "unit_end": 1,
            "resume_cursor": None,
        })
    else:
        common_request.update({
            "region_id": "region-smoke", "region_kind": "page",
            "element_kind_hint": "page", "content_kind_hint": "text",
            "normalized_text_ref": "runner-text:input", "structured_content_ref": None,
            "native_artifact_ref": None,
            "locator_draft": {"selector_kind": "page_region", "page_number": 1},
            "active_trait_hints": [],
        })
    smoke_input = {
        "artifact_base64": "U21va2UgYXJ0aWZhY3Q=", "request": common_request,
        "input_assets": {"runner-text:input": "U21va2UgdGV4dA=="},
    }
    _write(destination / "fixtures/smoke-input.json", smoke_input)
    _write(destination / "tests/fixtures/sample.json", smoke_input)
    _write(destination / "expected/smoke-output.json", {"drafts": []})
    _write(destination / "requirements.lock", "")
    _write(destination / "sbom.spdx.json", {"spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT", "name": plugin_id, "documentNamespace": f"https://atlas.local/spdx/{plugin_id}/0.1.0", "creationInfo": {"created": "1980-01-01T00:00:00Z", "creators": ["Tool: atlas-processing-sdk"]}, "documentDescribes": ["SPDXRef-Package-plugin"], "packages": [{"SPDXID": "SPDXRef-Package-plugin", "name": plugin_id, "versionInfo": "0.1.0", "downloadLocation": "NOASSERTION", "filesAnalyzed": False, "licenseConcluded": "Apache-2.0", "licenseDeclared": "Apache-2.0"}]})
