#!/usr/bin/env python3
"""Exercise the usable nine-format Atlas path without a browser or live VLM."""

from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import time

import httpx
from pypdf import PdfReader


PROJECT_ID = "project-multiformat-acceptance"
CONNECTION_ID = "conn-multiformat-acceptance"
ROUTE_ID = "route-multiformat-acceptance"
VISION_PROFILES = (
    "default-docx",
    "default-pptx",
    "default-xlsx",
    "default-doc",
    "default-ppt",
    "default-xls",
)
PROFILE_BY_FORMAT = {
    "pdf": "default-pdf",
    "docx": "default-docx",
    "pptx": "default-pptx",
    "xlsx": "default-xlsx",
    "txt": "default-text",
    "csv": "default-csv",
    "doc": "default-doc",
    "ppt": "default-ppt",
    "xls": "default-xls",
}
MIME_BY_FORMAT = {
    "pdf": "application/pdf",
    "docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation"
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    ),
    "txt": "text/plain",
    "csv": "text/csv",
    "doc": "application/msword",
    "ppt": "application/vnd.ms-powerpoint",
    "xls": "application/vnd.ms-excel",
}


def checked(response: httpx.Response, *statuses: int) -> httpx.Response:
    if response.status_code not in statuses:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}: {response.text[:2000]}"
        )
    return response


def runtime_policy() -> dict[str, object]:
    return {
        "schema_version": "model-route-runtime-policy-v8",
        "tokenizer_profile": "cl100k_base",
        "max_tool_executions": 12,
        "max_provider_invocations": 26,
        "max_reasoning_revision_cycles": 2,
        "max_catalog_pages": 5,
        "max_search_rounds": 6,
        "max_model_visible_items_per_turn": 40,
        "max_retrieval_repairs": 3,
        "max_schema_retries_per_turn": 3,
        "max_selected_anchor_pages_per_round": 20,
        "provider_invocation_timeout_seconds": 60,
        "tool_execution_timeout_seconds": 45,
        "turn_timeout_seconds": 240,
        "context_window_tokens": 400_000,
        "max_input_tokens_per_invocation": 272_000,
        "max_output_tokens_per_invocation": 16_000,
        "max_tool_result_tokens_per_execution": 64_000,
        "max_total_tokens_per_conversation": 1_000_000,
    }


def login(client: httpx.Client) -> None:
    email = os.environ.get("ATLAS_BOOTSTRAP_ADMIN_EMAIL")
    password = os.environ.get("ATLAS_BOOTSTRAP_ADMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "ATLAS_BOOTSTRAP_ADMIN_EMAIL and "
            "ATLAS_BOOTSTRAP_ADMIN_PASSWORD are required"
        )
    session = checked(
        client.post(
            "/api/v1/auth/sessions",
            json={
                "email": email,
                "password": password,
                "idempotency_key": "login-multiformat-acceptance",
            },
        ),
        200,
    ).json()
    if not session["authenticated"] or session["system_role"] != "admin":
        raise RuntimeError("acceptance login did not create an admin session")


def login_and_configure(client: httpx.Client) -> dict[str, int]:
    login(client)

    checked(
        client.post(
            "/api/v1/admin/projects",
            json={
                "project_id": PROJECT_ID,
                "name": "Multiformat acceptance",
                "policy_profile_id": "policy-default-governed",
                "idempotency_key": "create-project-multiformat-acceptance",
            },
        ),
        201,
    )
    connection = checked(
        client.post(
            "/api/v1/admin/config/provider-connections",
            json={
                "connection_id": CONNECTION_ID,
                "display_name": "Multiformat deterministic provider",
                "provider_type": "openai_compatible",
                "endpoint_url": "http://127.0.0.1:18081/v1",
                "api_key": "acceptance-only",
                "idempotency_key": "create-connection-multiformat-acceptance",
            },
        ),
        201,
    ).json()
    if connection["status"] != "verified" or not connection["enabled"]:
        raise RuntimeError("deterministic provider connection was not verified")
    route = checked(
        client.post(
            "/api/v1/admin/config/model-routes",
            json={
                "route_id": ROUTE_ID,
                "display_name": "Multiformat deterministic route",
                "model_name": "atlas-deterministic-acceptance",
                "connection_id": CONNECTION_ID,
                "enabled": True,
                "supports_vision": True,
                "runtime_policy": runtime_policy(),
                "idempotency_key": "create-route-multiformat-acceptance",
            },
        ),
        201,
    ).json()
    if (
        route["status"] != "test_passed"
        or not route["enabled"]
        or not route["supports_vision"]
    ):
        raise RuntimeError("deterministic route did not pass its controlled test")
    route = checked(
        client.post(
            f"/api/v1/admin/config/model-routes/{ROUTE_ID}/default",
            json={
                "expected_revision": route["revision"],
                "idempotency_key": "default-route-multiformat-acceptance",
            },
        ),
        200,
    ).json()
    if not route["is_default"]:
        raise RuntimeError("deterministic route was not selected for Workspace")

    profiles = checked(
        client.get("/api/v1/admin/processing-profiles"), 200
    ).json()["items"]
    revisions: dict[str, int] = {}
    for profile_id in VISION_PROFILES:
        profile = next(
            item for item in profiles if item["profile_id"] == profile_id
        )
        current_revision = max(item["revision"] for item in profile["revisions"])
        active = next(
            item for item in profile["revisions"] if item["status"] == "active"
        )
        key = f"create-{profile_id}-vision-acceptance"
        body = {
            field: active[field]
            for field in (
                "accepted_media_types",
                "base_parser_plugin_ref",
                "mandatory_processor_plugin_refs",
                "eligible_processor_plugin_refs",
                "plugin_priority",
                "planner_enabled",
                "planner_model_route_id",
                "channel_registry_version",
                "trait_registry_version",
                "max_regions_per_plan",
                "max_modules_per_region",
                "max_total_plugin_invocations",
                "planner_failure_behavior",
            )
        }
        body.update({"idempotency_key": key})
        created = checked(
            client.post(
                f"/api/v1/admin/processing-profiles/{profile_id}/revisions",
                json=body,
                headers={"If-Match": str(current_revision), "Idempotency-Key": key},
            ),
            201,
        ).json()
        revision = int(created["revision"])
        activate_key = f"activate-{profile_id}-vision-acceptance"
        activated = checked(
            client.post(
                (
                    f"/api/v1/admin/processing-profiles/{profile_id}/"
                    f"revisions/{revision}/activate"
                ),
                json={
                    "expected_revision": revision,
                    "idempotency_key": activate_key,
                },
                headers={
                    "If-Match": str(revision),
                    "Idempotency-Key": activate_key,
                },
            ),
            200,
        ).json()
        if activated["status"] != "active":
            raise RuntimeError(f"{profile_id} revision was not activated")
        revisions[profile_id] = revision
    return revisions


def login_and_verify_existing_configuration(
    client: httpx.Client,
) -> dict[str, int]:
    login(client)
    projects = checked(client.get("/api/v1/admin/projects"), 200).json()[
        "projects"
    ]
    if not any(item["project_id"] == PROJECT_ID for item in projects):
        raise RuntimeError("acceptance project is not configured")
    routes = checked(
        client.get("/api/v1/admin/config/model-routes"), 200
    ).json()["routes"]
    route = next((item for item in routes if item["route_id"] == ROUTE_ID), None)
    if (
        route is None
        or route["status"] != "test_passed"
        or not route["enabled"]
        or not route["supports_vision"]
        or not route["is_default"]
    ):
        raise RuntimeError("acceptance model route is not ready")
    profiles = checked(
        client.get("/api/v1/admin/processing-profiles"), 200
    ).json()["items"]
    revisions: dict[str, int] = {}
    for profile_id in VISION_PROFILES:
        profile = next(
            item for item in profiles if item["profile_id"] == profile_id
        )
        active = next(
            item for item in profile["revisions"] if item["status"] == "active"
        )
        revisions[profile_id] = int(active["revision"])
    return revisions


def upload_documents(
    client: httpx.Client, fixtures: Path, manifest: dict
) -> dict[str, dict[str, str]]:
    jobs: dict[str, dict[str, str]] = {}
    for document_format, item in manifest["files"].items():
        path = fixtures / item["filename"]
        if not path.is_file():
            raise RuntimeError(f"missing fixture: {path}")
        document_id = f"document-multiformat-{document_format}"
        with path.open("rb") as stream:
            response = checked(
                client.post(
                    "/api/v1/admin/document-library",
                    data={
                        "document_id": document_id,
                        "scope_type": "project",
                        "scope_id": PROJECT_ID,
                        "description": "Nine-format product acceptance fixture",
                        "allow_member_download": "false",
                        "idempotency_key": f"upload-multiformat-{document_format}",
                    },
                    files={
                        "file": (
                            path.name,
                            stream,
                            MIME_BY_FORMAT[document_format],
                        )
                    },
                    timeout=180,
                ),
                202,
            )
        body = response.json()
        actual_format = body["document"]["document_format"]
        if actual_format != document_format:
            raise RuntimeError(
                f"{path.name} dispatched as {actual_format}, expected {document_format}"
            )
        jobs[document_format] = {
            "job_id": body["job_id"],
            "status_url": body["status_url"],
            "document_id": document_id,
        }
    return jobs


def wait_for_documents(
    client: httpx.Client,
    jobs: dict[str, dict[str, str]],
    vision_revisions: dict[str, int],
    timeout_seconds: int,
) -> dict[str, dict]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, dict] = {}
    printed: dict[str, str] = {}
    while time.monotonic() < deadline:
        terminal = 0
        for document_format, job in jobs.items():
            status = checked(client.get(job["status_url"]), 200).json()
            latest[document_format] = status
            marker = f"{status['status']}:{status['current_stage']}"
            if printed.get(document_format) != marker:
                print(
                    f"{document_format}: {status['status']} "
                    f"({status['current_stage']})",
                    flush=True,
                )
                printed[document_format] = marker
            if status["status"] in {"ready", "ready_with_warnings", "failed"}:
                terminal += 1
        if terminal == len(jobs):
            break
        time.sleep(1.5)
    else:
        raise RuntimeError("nine-format processing did not reach terminal state")

    for document_format, status in latest.items():
        if status["status"] not in {"ready", "ready_with_warnings"}:
            raise RuntimeError(
                f"{document_format} failed: {status.get('failure_code')}"
            )
        expected_profile = PROFILE_BY_FORMAT[document_format]
        if status["profile_id"] != expected_profile:
            raise RuntimeError(
                f"{document_format} selected {status['profile_id']}, "
                f"expected {expected_profile}"
            )
        expected_revision = vision_revisions.get(expected_profile, 1)
        if status["profile_revision"] != expected_revision:
            raise RuntimeError(
                f"{document_format} pinned revision {status['profile_revision']}, "
                f"expected {expected_revision}"
            )

    documents = checked(
        client.get(
            "/api/v1/admin/document-library",
            params={"scope_type": "project", "scope_id": PROJECT_ID},
        ),
        200,
    ).json()["documents"]
    by_id = {item["document_id"]: item for item in documents}
    for document_format, job in jobs.items():
        document = by_id.get(job["document_id"])
        if document is None or document["evidence_count"] <= 0:
            raise RuntimeError(f"{document_format} has no searchable evidence")
    return latest


def create_conversation(client: httpx.Client, suffix: str) -> str:
    body = checked(
        client.post(
            "/api/v1/workspace/conversations",
            json={
                "scope_mode": "selected_tags",
                "tag_refs": [{"tag_type": "project", "tag_id": PROJECT_ID}],
                "title": f"Multiformat acceptance {suffix}",
                "idempotency_key": f"conversation-multiformat-{suffix}",
            },
        ),
        201,
    ).json()
    return body["conversation_id"]


def run_turn(
    client: httpx.Client, conversation_id: str, text: str, suffix: str
) -> dict:
    response = checked(
        client.post(
            f"/api/v1/workspace/conversations/{conversation_id}/turns",
            json={
                "input_text": text,
                "evidence_budget": 12,
                "idempotency_key": f"turn-multiformat-{suffix}",
            },
            timeout=150,
        ),
        200,
    )
    body = response.json()
    if body["execution_status"] != "completed":
        raise RuntimeError(
            f"Workspace turn did not complete: {body.get('refusal_code')}"
        )
    return body


def open_viewer(client: httpx.Client, turn: dict, citation: dict) -> tuple[dict, bytes]:
    if not citation["viewer_available"]:
        raise RuntimeError(f"citation has no Viewer: {citation['citation_id']}")
    manifest = checked(
        client.post(
            f"/api/v1/workspace/citations/{citation['citation_id']}/viewer-sessions"
        ),
        201,
    ).json()
    if manifest["assistant_turn_id"] != turn["turn_id"]:
        raise RuntimeError("Viewer session was not bound to the answer turn")
    item = next(
        value
        for value in manifest["viewer_items"]
        if value["viewer_item_id"] == manifest["initial_viewer_item_id"]
    )
    content = checked(client.get(item["content_endpoint"]), 200)
    if content.headers.get("cache-control") != "no-store":
        raise RuntimeError("Viewer content was not marked no-store")
    if content.headers.get("x-content-type-options") != "nosniff":
        raise RuntimeError("Viewer content was not marked nosniff")
    if "inline" not in content.headers.get("content-disposition", ""):
        raise RuntimeError("Viewer content was not served inline")
    if not content.headers.get("etag"):
        raise RuntimeError("Viewer content did not include an ETag")
    return {"manifest": manifest, "item": item, "headers": dict(content.headers)}, content.content


def verify_queries_and_viewer(
    client: httpx.Client, manifest: dict, *, run_suffix: str = ""
) -> dict[str, object]:
    suffix = f"-{run_suffix}" if run_suffix else ""
    conversation_id = create_conversation(client, f"cross-document{suffix}")
    cross = run_turn(
        client,
        conversation_id,
        manifest["complementary_query"],
        f"cross-document{suffix}",
    )
    answer = (cross.get("answer_text") or "").casefold()
    if not all(term.casefold() in answer for term in manifest["expected_answer_terms"]):
        raise RuntimeError(f"cross-document answer was incomplete: {answer}")
    formats = {item["document_format"] for item in cross["citations"]}
    titles = {item["document_title"] for item in cross["citations"]}
    if len(formats) < 2 or len(titles) < 2:
        raise RuntimeError("Workspace answer did not cite two documents and formats")
    if "pdf" not in formats or not formats.intersection({"doc", "docx"}):
        raise RuntimeError(
            f"complementary answer did not combine PDF and Word evidence: {formats}"
        )
    pdf_citation = next(item for item in cross["citations"] if item["document_format"] == "pdf")
    office_citation = next(
        item
        for item in cross["citations"]
        if item["document_format"] in {"doc", "docx"}
    )
    pdf_view, pdf_bytes = open_viewer(client, cross, pdf_citation)
    if (
        pdf_view["item"]["artifact_kind"] != "pdf_single_page"
        or not pdf_bytes.startswith(b"%PDF")
        or len(PdfReader(BytesIO(pdf_bytes)).pages) != 1
    ):
        raise RuntimeError("PDF citation did not open one immutable evidence page")
    office_view, office_bytes = open_viewer(client, cross, office_citation)
    if (
        office_view["item"]["artifact_kind"] != "page_image"
        or not office_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise RuntimeError("Office citation did not open a page-image PNG")

    visual_conversation = create_conversation(client, f"visual{suffix}")
    visual = run_turn(
        client,
        visual_conversation,
        manifest["visual_query"],
        f"visual{suffix}",
    )
    visual_citations = [
        item
        for item in visual["citations"]
        if item["evidence_modality"] == "visual_inference"
    ]
    if not visual_citations:
        raise RuntimeError("visual inference was not retrieved into Workspace")
    if visual["response_kind"] != "external_unverified" or not any(
        item["verification_status"] == "unverified_inference"
        for item in visual["response_segments"]
    ):
        raise RuntimeError("VLM-only claim was not kept unverified")
    visual_view, visual_bytes = open_viewer(client, visual, visual_citations[0])
    initial_citation = next(
        item
        for item in visual_view["manifest"]["citations"]
        if item["citation_id"] == visual_citations[0]["citation_id"]
    )
    if (
        visual_view["item"]["artifact_kind"] != "page_image"
        or not visual_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        or initial_citation["viewer_target"]["kind"]
        not in {"image_rectangle", "image_whole_page"}
    ):
        raise RuntimeError("visual citation did not open its verified Office page")

    return {
        "cross_document_formats": sorted(formats),
        "cross_document_titles": sorted(titles),
        "cross_document_citation_count": len(cross["citations"]),
        "pdf_viewer_bytes": len(pdf_bytes),
        "office_viewer_bytes": len(office_bytes),
        "office_target_kind": next(
            item["viewer_target"]["kind"]
            for item in office_view["manifest"]["citations"]
            if item["citation_id"] == office_citation["citation_id"]
        ),
        "visual_citation_count": len(visual_citations),
        "visual_target_kind": initial_citation["viewer_target"]["kind"],
    }


def processing_visual_call_count(url: str) -> int:
    response = checked(httpx.get(f"{url.rstrip('/')}/calls", timeout=10), 200)
    calls = response.json().get("calls")
    if not isinstance(calls, list):
        raise RuntimeError("processing provider did not return its call log")
    visual = [
        item
        for item in calls
        if isinstance(item, dict)
        and item.get("schema_name") == "atlas_office_visual_interpretation_v1"
    ]
    if not visual or any(
        item.get("has_image") is not True
        or not isinstance(item.get("image_digest"), str)
        or len(item["image_digest"]) != 64
        for item in visual
    ):
        raise RuntimeError("processing provider visual call evidence is incomplete")
    return len(visual)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8012")
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--processing-timeout-seconds", type=int, default=900)
    parser.add_argument("--journey-only", action="store_true")
    parser.add_argument("--run-suffix", default="")
    parser.add_argument(
        "--processing-provider-url",
        default="http://127.0.0.1:18082",
    )
    parser.add_argument("--expected-visual-provider-call-count", type=int)
    parser.add_argument("--configuration-ready", action="store_true")
    args = parser.parse_args()
    fixtures = args.fixtures.resolve()
    manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
    if set(manifest["files"]) != set(PROFILE_BY_FORMAT):
        raise RuntimeError("fixture manifest does not contain exactly nine formats")

    with httpx.Client(base_url=args.base_url, follow_redirects=False) as client:
        if args.journey_only:
            login(client)
            journey = verify_queries_and_viewer(
                client, manifest, run_suffix=args.run_suffix
            )
            visual_provider_call_count = processing_visual_call_count(
                args.processing_provider_url
            )
            if (
                args.expected_visual_provider_call_count is not None
                and visual_provider_call_count
                != args.expected_visual_provider_call_count
            ):
                raise RuntimeError(
                    "processing worker restart changed the visual provider call count"
                )
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "mode": "journey_only",
                        "visual_provider_call_count": visual_provider_call_count,
                        **journey,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0

        revisions = (
            login_and_verify_existing_configuration(client)
            if args.configuration_ready
            else login_and_configure(client)
        )
        jobs = upload_documents(client, fixtures, manifest)
        statuses = wait_for_documents(
            client, jobs, revisions, args.processing_timeout_seconds
        )
        journey = verify_queries_and_viewer(
            client, manifest, run_suffix=args.run_suffix
        )
        visual_provider_call_count = processing_visual_call_count(
            args.processing_provider_url
        )

    result = {
        "status": "passed",
        "formats": sorted(jobs),
        "document_statuses": {
            key: value["status"] for key, value in statuses.items()
        },
        "vision_profile_revisions": revisions,
        "visual_provider_call_count": visual_provider_call_count,
        **journey,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
