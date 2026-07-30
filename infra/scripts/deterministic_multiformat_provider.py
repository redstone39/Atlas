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

        if schema_name == "atlas_claim_validation_v1":
            payload = json.loads(messages[-1]["content"])
            output = {
                "assessments": [
                    {
                        "segment_id": candidate["segment_id"],
                        "status": "evidence_supported",
                        "reason_code": "supported_by_evidence",
                        "evidence_ids": [
                            item["evidence_id"] for item in candidate["evidence"]
                        ],
                    }
                    for candidate in payload["candidates"]
                ]
            }
            return output, "stop", {
                "content": json.dumps(output, separators=(",", ":")),
                "refusal": None,
            }

        if not any(item.get("role") == "tool" for item in messages):
            question = next(
                item["content"]
                for item in reversed(messages)
                if item.get("role") == "user"
            )
            arguments = json.dumps(
                {"query_text": question, "evidence_budget": 12},
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

        tool = json.loads(
            next(
                item["content"]
                for item in reversed(messages)
                if item.get("role") == "tool"
            )
        )
        result = tool["result"]
        _record({
            "schema_name": "atlas_workspace_tool_result",
            "status": result.get("status"),
            "evidence": [
                {
                    "evidence_id": item.get("evidence_id"),
                    "content": str(item.get("content", ""))[:300],
                }
                for item in result.get("evidence", [])
            ],
        })
        question = next(
            item["content"]
            for item in reversed(messages)
            if item.get("role") == "user"
        ).casefold()
        if result.get("status") != "completed":
            output = {
                "response_kind": "unknown",
                "segments": [
                    {
                        "segment_id": "unknown",
                        "kind": "unknown",
                        "text": "No supported answer.",
                        "citation_ids": [],
                        "claim_ids": [],
                        "evidence_unit_ids": [],
                        "external_unverified": False,
                    }
                ],
            }
        elif "visual bus" in question:
            output = Handler._grounded_answer(
                result,
                segment_id="visual-bus",
                text="ORION VISUAL BUS connects one controller to two sensors.",
                claim_id="claim-visual-bus",
                required_phrases=("orion visual bus",),
            )
        else:
            output = Handler._grounded_answer(
                result,
                segment_id="controller-spec",
                text=(
                    "The ORION controller target is 47 ohm and the qualification "
                    "tolerance is plus or minus 5 percent."
                ),
                claim_id="claim-controller-spec",
                required_phrases=("47 ohm", "plus or minus 5 percent"),
            )
        return output, "stop", {
            "content": json.dumps(output, ensure_ascii=False, separators=(",", ":")),
            "refusal": None,
        }

    @staticmethod
    def _grounded_answer(
        result: dict,
        *,
        segment_id: str,
        text: str,
        claim_id: str,
        required_phrases: tuple[str, ...],
    ) -> dict:
        selected_indices = [
            index
            for index, evidence in enumerate(result["evidence"])
            if any(
                phrase in str(evidence.get("content", "")).casefold()
                for phrase in required_phrases
            )
        ]
        matched_phrases = {
            phrase
            for index in selected_indices
            for phrase in required_phrases
            if phrase
            in str(result["evidence"][index].get("content", "")).casefold()
        }
        if matched_phrases != set(required_phrases):
            raise ValueError("required acceptance evidence was not retrieved")
        return {
            "response_kind": "grounded_answer",
            "segments": [
                {
                    "segment_id": segment_id,
                    "kind": "controlled",
                    "text": text,
                    "citation_ids": [
                        result["citations"][index]["citation_handle"]
                        for index in selected_indices
                    ],
                    "claim_ids": [claim_id],
                    "evidence_unit_ids": [
                        result["evidence"][index]["evidence_id"]
                        for index in selected_indices
                    ],
                    "external_unverified": False,
                }
            ],
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
