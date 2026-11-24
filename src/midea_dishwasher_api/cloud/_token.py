"""Pick a token and key out of a getToken ``tokenlist``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._json import JsonValue, expect_object
from ._udp_id import udp_id_candidates
from .cloud_error import CloudError
from .device_credentials import DeviceCredentials

if TYPE_CHECKING:
    from collections.abc import Callable

_TOKEN_LEN = 64
_KEY_LEN = 32


def first_credentials(
    device_id: int,
    fetch: Callable[[str], tuple[bytes, bytes] | None],
) -> DeviceCredentials:
    """Return the first UDP id whose getToken response carries a token."""
    for identifier in udp_id_candidates(device_id):
        found = fetch(identifier)
        if found is not None:
            token, key = found
            return DeviceCredentials(udp_id=identifier, token=token, key=key)
    msg = "Failed to fetch token: cloud returned no token for this device"
    raise CloudError(msg)


def match_token(data: JsonValue, udp: str) -> tuple[bytes, bytes] | None:
    """Return the token and key whose ``udpId`` matches, or None when none do."""
    body = expect_object(data, "data")
    items = body.get("tokenlist")
    if not isinstance(items, list):
        msg = "Failed to fetch token: tokenlist is missing"
        raise CloudError(msg)
    for item in items:
        if not isinstance(item, dict) or item.get("udpId") != udp:
            continue
        return _hex_field("token", item.get("token"), _TOKEN_LEN), _hex_field(
            "key", item.get("key"), _KEY_LEN
        )
    return None


def _hex_field(label: str, value: JsonValue | None, size: int) -> bytes:
    """Decode a hex secret and require the length the LAN transport checks."""
    if not isinstance(value, str):
        msg = f"Failed to fetch token: {label} is missing"
        raise CloudError(msg)
    try:
        raw = bytes.fromhex(value)
    except ValueError as error:
        msg = f"Failed to fetch token: {label} is not hex"
        raise CloudError(msg) from error
    if len(raw) != size:
        msg = f"Failed to fetch token: {label} must be {size} bytes (got {len(raw)})"
        raise CloudError(msg)
    return raw
