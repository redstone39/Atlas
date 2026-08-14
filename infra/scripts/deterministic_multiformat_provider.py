#!/usr/bin/env python3
"""Deterministic OpenAI-compatible provider used only by the Atlas acceptance smoke."""

from __future__ import annotations

import argparse
import base64
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock


CALLS: list[dict[str, object]] = []
CALLS_LOCK = Lock()
CALLS_FILE: Path | None = None


def _record(value: dict[str, object]) -> None:
    with CALLS_LOCK:
        CALLS.append(value)
        if CALLS_FILE is not None:
            CALLS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with CALLS_FILE.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )


def _load_calls(path: Path) -> None:
    if not path.exists():
        return
    loaded: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("provider call log must contain JSON objects")
        loaded.append(value)
    with CALLS_LOCK:
        CALLS.extend(loaded)


class Handler(BaseHTTPRequestHandler):
    server_version = "AtlasDeterministicProvider/1"

    def log_message(self, *_args) -> None:
        return None

    def _send_json(self, value: object, status: int = 200) -> None:
        data = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("calls"):
            with CALLS_LOCK:
                calls = list(CALLS)
            self._send_json({"calls": calls})
            return
        self._send_json({"data": [{"id": "atlas-deterministic-acceptance"}]})

    def do_POST(self) -> None:
        try:
            body = json.loads(
                self.rfile.read(int(self.headers.get("content-length", "0")))
            )
            schema_name = (
                body.get("response_format", {})
                .get("json_schema", {})
                .get("name", "")
            )
            messages = body.get("messages", [])
            output, finish_reason, message = self._response(
                schema_name, messages
            )
            _record({
                "schema_name": schema_name,
                "finish_reason": finish_reason,
                **output.pop("_call_metadata", {}),
            })
            self._send_json({
                "id": f"fake-{len(CALLS)}",
                "model": body.get("model", "atlas-deterministic-acceptance"),
                "choices": [{"finish_reason": finish_reason, "message": message}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                },
            })
        except Exception as exc:
            _record({
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:500],
            })
            self._send_json(
                {"error": {"message": type(exc).__name__, "type": "invalid_request"}},
                status=400,
            )

    @staticmethod
    def _response(
        schema_name: str, messages: list[dict]
    ) -> tuple[dict, str, dict]:
        if schema_name == "atlas_route_readiness_v1":
            output = {"status": "ready"}
            return output, "stop", {
                "content": json.dumps(output, separators=(",", ":")),
                "refusal": None,
            }

        user_text = " ".join(
            item["content"]
            for item in messages
            if item.get("role") == "user" and isinstance(item.get("content"), str)
        ).casefold()
        question = (
            "What does ORION VISUAL BUS connect?"
            if "visual bus" in user_text
            else (
                "For ORION, what values are listed for Target resistance "
                "and Qualification tolerance?"
            )
        )
        if schema_name == "context_resolver_v1":
            output = {"resolver_context": f"The current request asks: {question}"}
            return output, "stop", {
                "content": json.dumps(output, separators=(",", ":")),
                "refusal": None,
            }
        if schema_name == "context_rewrite_v1":
            output = {"rewritten_question": question}
            return output, "stop", {
                "content": json.dumps(output, separators=(",", ":")),
                "refusal": None,
            }
        if schema_name == "atlas_initial_plan_decision_v1":
            output = {
                "next_objective": "Retrieve the authorized ORION evidence.",
                "completion_condition": "Answer from retrieved evidence.",
                "item_summaries": ["Search the authorized knowledge scope."],
            }
            return output, "stop", {
                "content": json.dumps(output, separators=(",", ":")),
                "refusal": None,
            }
        if schema_name == "atlas_replan_decision_v1":
            output = {
                "next_objective": "Finalize the supported answer.",
                "completion_condition": "The answer cites retrieved evidence.",
                "completed_item_ids": [],
                "skipped_item_ids": [],
                "new_item_summaries": [],
            }
            return output, "stop", {
                "content": json.dumps(output, separators=(",", ":")),
                "refusal": None,
            }
        if schema_name == "atlas_process_evaluation_decision_v2":
            output = {
                "verdict": "accept",
                "summary": "The answer is supported by retrieved evidence.",
                "rubric_dimensions": {
                    "plan_coverage": 2,
                    "evidence_handling": 2,
                    "conflict_handling": 2,
                    "gap_resolution": 2,
                    "revision_completion": 2,
                },
            }
            return output, "stop", {
                "content": json.dumps(output, separators=(",", ":")),
                "refusal": None,
            }

        if schema_name == "atlas_office_visual_interpretation_v1":
            user_content = messages[-1].get("content")
            if not isinstance(user_content, list):
                raise ValueError("visual request is not multimodal")
            image_parts = [
                part for part in user_content
                if part.get("type") == "image_url"
            ]
            if len(image_parts) != 1:
                raise ValueError("visual request requires exactly one image")
            url = image_parts[0].get("image_url", {}).get("url", "")
            prefix = "data:image/png;base64,"
            if not isinstance(url, str) or not url.startswith(prefix):
                raise ValueError("visual request is not PNG")
            image = base64.b64decode(url.removeprefix(prefix), validate=True)
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("visual request has invalid PNG bytes")
            output = {
                "visual_type": "diagram",
                "summary": (
                    "ORION VISUAL BUS connects one controller to two sensors."
                ),
                "visible_text": ["ORION CTRL", "SENSOR A", "SENSOR B"],
                "entities": ["controller", "sensor A", "sensor B"],
                "relationships": [
                    {
                        "subject": "controller",
                        "relation": "connects to",
                        "object": "sensor A",
                    },
                    {
                        "subject": "controller",
                        "relation": "connects to",
                        "object": "sensor B",
                    },
                ],
                "chart_observations": [],
                "uncertainties": ["deterministic acceptance provider"],
                "_call_metadata": {
                    "has_image": True,
                    "image_digest": sha256(image).hexdigest(),
                },
            }
            provider_output = {
                key: value for key, value in output.items()
                if key != "_call_metadata"
            }
            return output, "stop", {
                "content": json.dumps(
                    provider_output,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "refusal": None,
            }

        if schema_name == "provisional_declared_evidence_decision_v3":
            payload = next(
                json.loads(item["content"])
                for item in messages
                if item.get("role") == "user"
                and isinstance(item.get("content"), str)
                and item["content"].startswith("{")
            )
            image_parts = [
                part
                for item in messages
                if isinstance(item.get("content"), list)
                for part in item["content"]
                if part.get("type") == "image_url"
            ]
            output = {
                "item_outcomes": ["aligned"] * len(payload["answer_items"])
            }
            if image_parts:
                url = image_parts[0].get("image_url", {}).get("url", "")
                prefix = "data:image/png;base64,"
                if not isinstance(url, str) or not url.startswith(prefix):
                    raise ValueError("assessment visual request is not PNG")
                image = base64.b64decode(url.removeprefix(prefix), validate=True)
                output["_call_metadata"] = {
                    "has_image": True,
                    "image_digest": sha256(image).hexdigest(),
                }
            provider_output = {
                key: value
                for key, value in output.items()
                if key != "_call_metadata"
            }
            return output, "stop", {
                "content": json.dumps(provider_output, separators=(",", ":")),
                "refusal": None,
            }


        if not any(item.get("role") == "tool" for item in messages):
            arguments = json.dumps(
                {
                    "action": "discover_relevant_documents",
                    "query_text": question,
                    "limit": 20,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return {}, "tool_calls", {
                "content": None,
                "tool_calls": [
                    {
                        "id": "discover-multiformat",
                        "type": "function",
                        "function": {
                            "name": "discover_relevant_documents",
                            "arguments": arguments,
                        },
                    }
                ],
            }

        tool = json.loads(
            next(
                item["content"]
                for item in reversed(messages)
                if item.get("role") == "tool"
            )
        )
        _record({"schema_name": "atlas_workspace_tool_raw", "tool": tool})
        result = tool.get("result") or tool.get("observation", {})
        if result.get("result_type") == "relevant_document_discovery_result":
            document_handles = [
                item["document_handle"] for item in result.get("candidates", [])
            ]
            arguments = json.dumps(
                {
                    "action": "search_knowledge",
                    "query_text": question,
                    "document_handles": document_handles,
                    "required_modalities": [],
                    "facet_hints": {
                        "document_types": [],
                        "date_from": None,
                        "date_to": None,
                        "languages": [],
                        "tags": [],
                    },
                    "limit": 20,
                    "max_output_tokens": 64000,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return {}, "tool_calls", {
                "content": None,
                "tool_calls": [
                    {
                        "id": "search-multiformat",
                        "type": "function",
                        "function": {
                            "name": "search_knowledge",
                            "arguments": arguments,
                        },
                    }
                ],
            }
        if result.get("result_type") == "visual_inspection_result":
            output = {
                "action": "finalize_answer",
                "segments": [
                    {
                        "segment_id": "visual-bus",
                        "text": (
                            "ORION VISUAL BUS connects one controller to two sensors."
                        ),
                    }
                ],
                "claimed_evidence_handles": [result["visual_handle"]],
            }
            return output, "stop", {
                "content": json.dumps(output, separators=(",", ":")),
                "refusal": None,
            }
        if result.get("result_type") == "knowledge_search_result":
            if "visual bus" in question.casefold():
                visual_source = next(
                    item
                    for item in result.get("evidence", [])
                    if item["document_display_name"] == "orion-target"
                    and item.get("page_handle")
                )
                arguments = json.dumps(
                    {
                        "action": "inspect_visual",
                        "handle": visual_source["page_handle"],
                        "scope": "full",
                        "bbox": None,
                    },
                    separators=(",", ":"),
                )
                return {}, "tool_calls", {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "inspect-visual-multiformat",
                            "type": "function",
                            "function": {
                                "name": "inspect_visual",
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            expected_text = (
                "ORION VISUAL BUS connects one controller to two sensors."
                if "visual bus" in question.casefold()
                else (
                    "The ORION controller target is 47 ohm and the qualification "
                    "tolerance is plus or minus 5 percent."
                )
            )
            required_phrases = (
                ("orion visual bus",)
                if "visual bus" in question.casefold()
                else ("47 ohm", "plus or minus 5 percent")
            )
            evidence = result.get("evidence", [])
            selected = [
                item
                for item in evidence
                if any(
                    phrase in str(item.get("snippet", "")).casefold()
                    for phrase in required_phrases
                )
            ]
            matched = {
                phrase
                for item in selected
                for phrase in required_phrases
                if phrase in str(item.get("snippet", "")).casefold()
            }
            if matched != set(required_phrases):
                raise ValueError("required acceptance evidence was not retrieved")
            output = {
                "action": "finalize_answer",
                "segments": [
                    {
                        "segment_id": (
                            "visual-bus"
                            if "visual bus" in question.casefold()
                            else "controller-spec"
                        ),
                        "text": expected_text,
                    }
                ],
                "claimed_evidence_handles": list(
                    dict.fromkeys(item["evidence_handle"] for item in selected)
                ),
            }
            return output, "stop", {
                "content": json.dumps(
                    output, ensure_ascii=False, separators=(",", ":")
                ),
                "refusal": None,
            }
        output = {
            "action": "finalize_answer",
            "segments": [
                {
                    "segment_id": "unknown",
                    "text": "No supported answer.",
                }
            ],
            "claimed_evidence_handles": [],
        }
        return output, "stop", {
            "content": json.dumps(output, separators=(",", ":")),
            "refusal": None,
        }


def main() -> int:
    global CALLS_FILE
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--calls-file", type=Path)
    args = parser.parse_args()
    if args.calls_file is not None:
        CALLS_FILE = args.calls_file.resolve()
        _load_calls(CALLS_FILE)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
