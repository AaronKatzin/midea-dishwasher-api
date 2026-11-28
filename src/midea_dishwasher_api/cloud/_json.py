"""JSON helpers that stay inside the types this package allows."""

from __future__ import annotations

import json

from .cloud_error import CloudError

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]


def loads(raw: bytes) -> JsonValue:
    """Parse a cloud response body."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        msg = "Failed to parse cloud response: invalid JSON"
        raise CloudError(msg) from error
    return _convert(parsed)


def expect_object(value: JsonValue | None, what: str) -> JsonObject:
    """Return a JSON object, or fail when the cloud left it out."""
    if not isinstance(value, dict):
        msg = f"Failed to parse cloud response: {what} is missing"
        raise CloudError(msg)
    return value


def expect_str(value: JsonValue | None, what: str) -> str:
    """Return a JSON string, or fail when the cloud left it out."""
    if not isinstance(value, str) or value == "":
        msg = f"Failed to parse cloud response: {what} is missing"
        raise CloudError(msg)
    return value


def expect_code(value: JsonValue | None) -> int:
    """Return an integer status code from a string or a number."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        msg = "Failed to parse cloud response: code is missing"
        raise CloudError(msg)
    try:
        return int(value)
    except ValueError as error:
        msg = "Failed to parse cloud response: code is missing"
        raise CloudError(msg) from error


def _convert(value: object) -> JsonValue:
    """Walk a decoded JSON value into the closed ``JsonValue`` union."""
    if value is None or isinstance(value, str | float | bool | int):
        return value
    if isinstance(value, list):
        return [_convert(item) for item in value]
    if isinstance(value, dict):
        converted: JsonObject = {}
        for key, item in value.items():
            if isinstance(key, str):
                converted[key] = _convert(item)
        return converted
    msg = "Failed to parse cloud response: unsupported JSON value"
    raise CloudError(msg)
