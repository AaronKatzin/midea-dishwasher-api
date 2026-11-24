"""
UDP id derivation used by ``/v1/iot/secure/getToken``.

The cloud does not take the decimal device id. msmart-ng folds the SHA-256 of
the 6-byte id (each half XOR the other) and sends that digest as hex. Discovery
encodes the id little-endian; some appliances only match the big-endian form,
so callers try both.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

_ID_BYTES = 6
_HALF = 16


def udp_id(device_id: int, *, byteorder: Literal["little", "big"] = "little") -> str:
    """Return the hex UDP id ``getToken`` expects for a device id."""
    try:
        raw = device_id.to_bytes(_ID_BYTES, byteorder)
    except OverflowError as error:
        msg = f"Failed to build UDP id: device_id does not fit in {_ID_BYTES} bytes"
        raise ValueError(msg) from error
    digest = sha256(raw).digest()
    folded = bytes(left ^ right for left, right in zip(digest[:_HALF], digest[_HALF:], strict=True))
    return folded.hex()


def udp_id_candidates(device_id: int) -> tuple[str, str]:
    """Return the little-endian UDP id, then the big-endian one."""
    return (udp_id(device_id, byteorder="little"), udp_id(device_id, byteorder="big"))
