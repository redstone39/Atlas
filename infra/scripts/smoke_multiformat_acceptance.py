#!/usr/bin/env python3
"""Exercise the usable nine-format Atlas path without a browser or live VLM."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import httpx


PROJECT_NAME = "Multiformat acceptance"
CONNECTION_NAME = "Multiformat deterministic provider"
ROUTE_NAME = "Multiformat deterministic route"
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
        "max_provider_invocations": 33,
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
    email = os.environ.get("ATLAS_MULTIFORMAT_ADMIN_EMAIL")
    password = os.environ.get("ATLAS_MULTIFORMAT_ADMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "ATLAS_MULTIFORMAT_ADMIN_EMAIL and "
            "ATLAS_MULTIFORMAT_ADMIN_PASSWORD are required"
        )
    session = checked(
        client.post(
            "/api/v1/auth/sessions",
            json={
                "identifier": email,
                "password": password,
            },
        ),
        200,
    ).json()
    if not session["authenticated"] or session["system_role"] != "admin":
        raise RuntimeError("acceptance login did not create an admin session")

def claim_first_admin(client: httpx.Client) -> None:
    email = os.environ.get("ATLAS_MULTIFORMAT_ADMIN_EMAIL")
    password = os.environ.get("ATLAS_MULTIFORMAT_ADMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "ATLAS_MULTIFORMAT_ADMIN_EMAIL and "
            "ATLAS_MULTIFORMAT_ADMIN_PASSWORD are required"
        )
    status = checked(client.get("/api/v1/auth/first-admin"), 200).json()
    if status != {"claim_available": True}:
        raise RuntimeError("fresh acceptance stack cannot claim the first administrator")
    session = checked(
        client.post(
            "/api/v1/auth/first-admin",
            json={
                "display_name": "Multiformat Acceptance Admin",
                "email": email,
                "password": password,
            },
        ),
        201,
    ).json()
    if not session["authenticated"] or session["system_role"] != "admin":
        raise RuntimeError("first-administrator claim did not create an admin session")



def login_and_configure(
    client: httpx.Client,
) -> tuple[str, dict[str, int]]:
    claim_first_admin(client)

    project = checked(
        client.post(
            "/api/v1/admin/projects",
            json={
                "name": PROJECT_NAME,
                "policy_profile_id": "policy-default-governed",
                "idempotency_key": "create-project-multiformat-acceptance",
            },
        ),
        201,
    ).json()
    target_ref = project["target_ref"]
    if not target_ref.startswith("project:"):
        raise RuntimeError("acceptance project owner returned an invalid target_ref")
    project_id = target_ref.split(":", 1)[1]
    connection = checked(
        client.post(
            "/api/v1/admin/config/provider-connections",
            json={
                "display_name": CONNECTION_NAME,
                "provider_type": "openai_compatible",
                "endpoint_url": "http://127.0.0.1:18081/v1",
                "api_key": "acceptance-only",
                "idempotency_key": "create-connection-multiformat-acceptance",
            },
        ),
        201,
    ).json()
    connection_id = connection["connection_id"]
    if connection["status"] != "verified" or not connection["enabled"]:
        raise RuntimeError("deterministic provider connection was not verified")
    route = checked(
        client.post(
            "/api/v1/admin/config/model-routes",
            json={
                "display_name": ROUTE_NAME,
                "model_name": "atlas-deterministic-acceptance",
                "connection_id": connection_id,
                "enabled": True,
                "supports_vision": True,
                "runtime_policy": runtime_policy(),
                "idempotency_key": "create-route-multiformat-acceptance",
            },
        ),
        201,
    ).json()
    route_id = route["route_id"]
    if (
        route["status"] != "test_passed"
        or not route["enabled"]
        or not route["supports_vision"]
    ):
        raise RuntimeError("deterministic route did not pass its controlled test")
    for purpose in ("text", "vision"):
        route = checked(
            client.post(
                f"/api/v1/admin/config/model-routes/{route_id}/defaults/{purpose}",
                json={
                    "expected_revision": route["revision"],
                    "idempotency_key": (
                        f"default-{purpose}-route-multiformat-acceptance"
                    ),
                },
            ),
            200,
        ).json()
    if not route["is_text_default"] or not route["is_vision_default"]:
        raise RuntimeError(
            "deterministic route was not selected for text and vision"
        )

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
    return project_id, revisions


def login_and_verify_existing_configuration(
    client: httpx.Client,
) -> tuple[str, dict[str, int]]:
    login(client)
    projects = checked(client.get("/api/v1/admin/projects"), 200).json()[
        "projects"
    ]
    matching_projects = [
        item
        for item in projects
        if item["name"] == PROJECT_NAME and item["status"] == "active"
    ]
    if len(matching_projects) != 1:
        raise RuntimeError(
            "acceptance project name must resolve to exactly one active project"
        )
    project_id = matching_projects[0]["project_id"]
    routes = checked(
        client.get("/api/v1/admin/config/model-routes"), 200
    ).json()["routes"]
    matching_routes = [
        item
        for item in routes
        if item["display_name"] == ROUTE_NAME
        and item["status"] == "test_passed"
        and item["enabled"]
        and item["supports_vision"]
        and item["is_text_default"]
        and item["is_vision_default"]
    ]
    if len(matching_routes) != 1:
        raise RuntimeError(
            "acceptance model route name must resolve to exactly one ready route"
        )
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
    return project_id, revisions


def resolve_acceptance_project(client: httpx.Client) -> str:
    projects = checked(client.get("/api/v1/admin/projects"), 200).json()[
        "projects"
    ]
    matches = [
        item
        for item in projects
        if item["name"] == PROJECT_NAME and item["status"] == "active"
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "acceptance project name must resolve to exactly one active project"
        )
    return matches[0]["project_id"]


def upload_documents(
    client: httpx.Client, fixtures: Path, manifest: dict, project_id: str
) -> dict[str, dict[str, str]]:
    jobs: dict[str, dict[str, str]] = {}
    for document_format, item in manifest["files"].items():
        path = fixtures / item["filename"]
        if not path.is_file():
            raise RuntimeError(f"missing fixture: {path}")
        with path.open("rb") as stream:
            response = checked(
                client.post(
                    "/api/v1/admin/document-library",
                    data={
                        "scope_type": "project",
                        "scope_id": project_id,
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
            "document_id": body["document"]["document_id"],
        }
    return jobs


def wait_for_documents(
    client: httpx.Client,
    jobs: dict[str, dict[str, str]],
    vision_revisions: dict[str, int],
    timeout_seconds: int,
    project_id: str,
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
            params={"scope_type": "project", "scope_id": project_id},
        ),
        200,
    ).json()["documents"]
    by_id = {item["document_id"]: item for item in documents}
    for document_format, job in jobs.items():
        document = by_id.get(job["document_id"])
        if document is None or document["evidence_count"] <= 0:
            raise RuntimeError(f"{document_format} has no searchable evidence")
    return latest


def create_conversation(
    client: httpx.Client, project_id: str, suffix: str
) -> str:
    body = checked(
        client.post(
            "/api/v1/workspace/conversations",
            json={
                "tag_refs": [{"tag_type": "project", "tag_id": project_id}],
                "title": f"Multiformat acceptance {suffix}",
                "idempotency_key": f"conversation-multiformat-{suffix}",
            },
        ),
        200,
    ).json()
    return body["conversation"]["conversation_id"]


def run_turn(
    client: httpx.Client, conversation_id: str, text: str, suffix: str
) -> dict:
    accepted = checked(
        client.post(
            f"/api/v1/workspace/conversations/{conversation_id}/turns",
            json={
                "input_text": text,
                "idempotency_key": f"turn-multiformat-{suffix}",
                "reasoning_mode": "standard",
            },
            timeout=150,
        ),
        202,
    ).json()
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        status = checked(
            client.get(accepted["status_url"], timeout=30),
            200,
        ).json()
        if status["state"] == "terminal_completed":
            detail = checked(
                client.get(
                    f"/api/v1/workspace/conversations/{conversation_id}",
                    timeout=30,
                ),
                200,
            ).json()
            turn = next(
                item
                for item in detail["turns"]
                if item["turn_id"] == accepted["turn_id"]
            )
            turn["answer_text"] = " ".join(
                segment["text"] for segment in turn["segments"]
            )
            return turn
        if status["state"] in {"terminal_failed", "lease_closed"}:
            raise RuntimeError(
                f"Workspace turn did not complete: {json.dumps(status)}"
            )
        time.sleep(0.25)
    raise RuntimeError("workspace turn timed out")


def verify_queries_and_viewer(
    client: httpx.Client,
    manifest: dict,
    project_id: str,
    *,
    run_suffix: str = "",
) -> dict[str, object]:
    suffix = f"-{run_suffix}" if run_suffix else ""
    format_by_title = {
        Path(item["filename"]).stem: document_format
        for document_format, item in manifest["files"].items()
    }

    def resolved_evidence(turn: dict) -> list[dict]:
        evidence = [
            item
            for item in turn["model_claimed_evidence"]
            if item["resolution_status"] == "resolved"
            and item["duplicate_of_position"] is None
        ]
        if not evidence:
            raise RuntimeError("Workspace answer did not resolve declared evidence")
        return evidence

    def open_evidence(conversation_id: str, turn: dict, item: dict) -> dict:
        return checked(
            client.get(
                (
                    f"/api/v1/workspace/conversations/{conversation_id}/turns/"
                    f"{turn['turn_id']}/declared-evidence/"
                    f"{item['protected_open_ref']}"
                ),
                headers={"Accept": "application/json"},
            ),
            200,
        ).json()

    conversation_id = create_conversation(
        client, project_id, f"cross-document{suffix}"
    )
    cross = run_turn(
        client,
        conversation_id,
        manifest["complementary_query"],
        f"cross-document{suffix}",
    )
    answer = (cross.get("answer_text") or "").casefold()
    if not all(term.casefold() in answer for term in manifest["expected_answer_terms"]):
        raise RuntimeError(f"cross-document answer was incomplete: {answer}")
    cross_evidence = resolved_evidence(cross)
    titles = {item["document_display_name"] for item in cross_evidence}
    formats = {
        format_by_title[title] for title in titles if title in format_by_title
    }
    if len(formats) < 2 or len(titles) < 2:
        raise RuntimeError(
            "Workspace answer did not declare two current documents and formats"
        )
    if "pdf" not in formats or not formats.intersection({"doc", "docx"}):
        raise RuntimeError(
            f"complementary answer did not combine PDF and Word evidence: {formats}"
        )
    pdf_evidence = next(
        item
        for item in cross_evidence
        if format_by_title.get(item["document_display_name"]) == "pdf"
    )
    word_evidence = next(
        item
        for item in cross_evidence
        if format_by_title.get(item["document_display_name"]) in {"doc", "docx"}
    )
    pdf_open = open_evidence(conversation_id, cross, pdf_evidence)
    word_open = open_evidence(conversation_id, cross, word_evidence)
    if "47 ohm" not in pdf_open["content"].casefold():
        raise RuntimeError("protected PDF evidence did not preserve target resistance")
    if "plus or minus 5 percent" not in word_open["content"].casefold():
        raise RuntimeError("protected Word evidence did not preserve qualification")

    visual_conversation = create_conversation(
        client, project_id, f"visual{suffix}"
    )
    visual = run_turn(
        client,
        visual_conversation,
        manifest["visual_query"],
        f"visual{suffix}",
    )
    visual_answer = (visual.get("answer_text") or "").casefold()
    if "one controller to two sensors" not in visual_answer:
        raise RuntimeError(f"visual answer was incomplete: {visual_answer}")
    visual_evidence = resolved_evidence(visual)
    visual_open = [
        open_evidence(visual_conversation, visual, item)
        for item in visual_evidence
    ]
    visual_items = [
        (declared, opened)
        for declared, opened in zip(visual_evidence, visual_open, strict=True)
        if declared["handle_kind"] == "visual"
        and opened["modality"] == "figure"
    ]
    if not visual_items or not all(
        "image digest " in opened["content"].casefold()
        for _, opened in visual_items
    ):
        raise RuntimeError("protected visual evidence was not retrievable")

    return {
        "cross_document_formats": sorted(formats),
        "cross_document_titles": sorted(titles),
        "cross_document_evidence_count": len(cross_evidence),
        "cross_document_review_status": cross["evidence_review_status"],
        "visual_evidence_count": len(visual_items),
        "visual_review_status": visual["evidence_review_status"],
    }




def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8012")
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--processing-timeout-seconds", type=int, default=900)
    parser.add_argument("--journey-only", action="store_true")
    parser.add_argument("--run-suffix", default="")
    parser.add_argument("--configuration-ready", action="store_true")
    args = parser.parse_args()
    fixtures = args.fixtures.resolve()
    manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
    if set(manifest["files"]) != set(PROFILE_BY_FORMAT):
        raise RuntimeError("fixture manifest does not contain exactly nine formats")

    with httpx.Client(base_url=args.base_url, follow_redirects=False) as client:
        if args.journey_only:
            login(client)
            project_id = resolve_acceptance_project(client)
            journey = verify_queries_and_viewer(
                client, manifest, project_id, run_suffix=args.run_suffix
            )
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "mode": "journey_only",
                        **journey,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0

        project_id, revisions = (
            login_and_verify_existing_configuration(client)
            if args.configuration_ready
            else login_and_configure(client)
        )
        jobs = upload_documents(client, fixtures, manifest, project_id)
        statuses = wait_for_documents(
            client,
            jobs,
            revisions,
            args.processing_timeout_seconds,
            project_id,
        )
        journey = verify_queries_and_viewer(
            client, manifest, project_id, run_suffix=args.run_suffix
        )

    result = {
        "status": "passed",
        "formats": sorted(jobs),
        "document_statuses": {
            key: value["status"] for key, value in statuses.items()
        },
        "vision_profile_revisions": revisions,
        **journey,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
