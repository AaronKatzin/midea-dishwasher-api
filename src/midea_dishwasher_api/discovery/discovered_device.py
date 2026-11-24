"""One appliance that answered the LAN discovery broadcast."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    """Identity fields carried by a version-2 or version-3 discovery reply."""

    ip: str
    device_id: int
    device_type: int
    protocol: int
    port: int
