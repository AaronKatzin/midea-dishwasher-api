"""LAN credentials returned by the Midea cloud for one appliance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeviceCredentials:
    """Token and key ready to pass to ``V3Transport``, plus the UDP id that matched."""

    udp_id: str
    token: bytes
    key: bytes
