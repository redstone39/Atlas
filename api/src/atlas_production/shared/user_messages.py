from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


MessageParam = str | int | float | bool | None
MessageParams = dict[str, MessageParam]


class UserMessageContractError(ValueError):
    """Raised when a server-authored user message violates the catalog."""


def _catalog_path() -> Path:
    configured = os.environ.get("ATLAS_USER_MESSAGES_CATALOG")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(__file__).resolve().parents[4] / "contracts" / "user-messages.json",
        Path("/contracts/user-messages.json"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise UserMessageContractError("Atlas user-message catalog is unavailable")


@lru_cache(maxsize=1)
def user_message_catalog() -> dict[str, Any]:
    try:
        payload = json.loads(_catalog_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UserMessageContractError("Atlas user-message catalog is invalid") from exc
    if payload.get("schema_version") != 1 or not isinstance(payload.get("messages"), dict):
        raise UserMessageContractError("Atlas user-message catalog has an unsupported schema")
    return payload["messages"]


def _matches_type(value: MessageParam, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    return False


def validate_message_reference(
    message_code: str,
    message_params: dict[str, Any] | None,
) -> MessageParams:
    if not isinstance(message_code, str) or not message_code:
        raise UserMessageContractError("message_code must be non-empty text")
    contract = user_message_catalog().get(message_code)
    if not isinstance(contract, dict):
        raise UserMessageContractError(f"unknown message_code: {message_code}")
    if not isinstance(message_params, dict):
        raise UserMessageContractError("message_params must be an object")
    parameter_contract = contract.get("params")
    if not isinstance(parameter_contract, dict):
        raise UserMessageContractError(f"invalid catalog entry: {message_code}")
    unknown = frozenset(message_params) - frozenset(parameter_contract)
    if unknown:
        raise UserMessageContractError(
            f"unexpected message_params for {message_code}: {sorted(unknown)}"
        )
    missing = sorted(
        name
        for name, specification in parameter_contract.items()
        if specification.get("required", False) and name not in message_params
    )
    if missing:
        raise UserMessageContractError(
            f"missing message_params for {message_code}: {missing}"
        )
    validated: MessageParams = {}
    for name, value in message_params.items():
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise UserMessageContractError(
                f"message_param {name} for {message_code} is not a safe primitive"
            )
        specification = parameter_contract[name]
        expected = specification.get("type")
        expected_types = [expected] if isinstance(expected, str) else expected
        if not isinstance(expected_types, list) or not expected_types or not all(
            isinstance(item, str) for item in expected_types
        ):
            raise UserMessageContractError(f"invalid catalog param: {message_code}.{name}")
        if not any(_matches_type(value, item) for item in expected_types):
            raise UserMessageContractError(
                f"wrong message_param type for {message_code}.{name}"
            )
        validated[name] = value
    return validated


class MessageReferenceModel(BaseModel):
    message_code: str
    message_params: MessageParams = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_message_reference(self):
        self.message_params = validate_message_reference(
            self.message_code,
            self.message_params,
        )
        return self
