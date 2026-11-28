"""
Midea LAN UDP discovery on ports 6445 and 20086.

The probe and the reply layout are the ones used by msmart-ng, midea-local
and midea-beautiful-air. Version 2 replies start with ``5A5A``; version 3
replies wrap that packet in an ``8370`` header. The encrypted payload uses
the same AES-ECB key as the V2 layer. Version 1 XML broadcasts are ignored:
their device id is not in the datagram.
"""

from __future__ import annotations

import logging
import socket
import time

from ..security import V2_ENC_KEY, aes_ecb_decrypt
from ..security.crypto import pkcs7_unpad
from .discovered_device import DiscoveredDevice
from .discovery_error import DiscoveryError

log: logging.Logger = logging.getLogger("midea_dishwasher_api.discovery")

_DISCOVERY_PORTS: tuple[int, ...] = (6445, 20086)
_LISTEN_ADDRESS: tuple[str, int] = ("0.0.0.0", 0)
# Fixed probe. Same 72 bytes as msmart ``DISCOVERY_MSG`` and midea-local ``BROADCAST_MSG``.
_DISCOVERY_MESSAGE = bytes.fromhex(
    "5a5a011148009200"
    "0000000000000000"
    "0000000000000000"
    "0000000000000000"
    "0000000000000000"
    "7f75bd6b3e4f8b76"
    "2e849c6e578d6590"
    "036e9d4342a50f1f"
    "569eb8ec918e92e5"
)
_V2_MAGIC = b"\x5a\x5a"
_V3_MAGIC = b"\x83\x70"
_V2_HEADER_LEN = 40
_V2_TRAILER_LEN = 16
_V3_HEADER_LEN = 8
_DEVICE_ID_SLICE = slice(20, 26)
_MIN_V2_LENGTH = _V2_HEADER_LEN + 16 + _V2_TRAILER_LEN
_SSID_LENGTH_OFFSET = 40
_SSID_OFFSET = 41
_AES_BLOCK = 16


def discover(
    *,
    timeout: float = 5.0,
    address: str = "255.255.255.255",
    packets: int = 3,
    device_type: int | None = None,
) -> list[DiscoveredDevice]:
    """
    Broadcast the discovery probe and return every appliance that answers.

    ``ip`` is the address the reply came from. ``protocol`` is ``2`` or ``3``.
    ``device_type`` keeps only that appliance type (``0xE1`` for a dishwasher)
    when it is set. Pass a subnet broadcast in ``address`` when the host has
    more than one interface.
    """
    if timeout <= 0:
        msg = "Failed to discover devices: timeout must be positive"
        raise ValueError(msg)
    if packets < 1:
        msg = "Failed to discover devices: packets must be at least 1"
        raise ValueError(msg)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(_LISTEN_ADDRESS)
            _send_probes(sock, address, packets)
            found = _collect(sock, timeout, device_type)
        finally:
            sock.close()
    except OSError as error:
        msg = f"Failed to discover devices: {error}"
        raise DiscoveryError(msg) from error
    log.info("discovery finished with %d device(s)", len(found))
    return found


def _send_probes(sock: socket.socket, address: str, packets: int) -> None:
    """Send the probe to both discovery ports."""
    for port in _DISCOVERY_PORTS:
        log.debug("discovery probe to %s:%d x%d", address, port, packets)
        for _repeat in range(packets):
            sock.sendto(_DISCOVERY_MESSAGE, (address, port))


def _collect(
    sock: socket.socket,
    timeout: float,
    device_type: int | None,
) -> list[DiscoveredDevice]:
    """Read replies until the deadline, keeping the first of each device id."""
    deadline = time.monotonic() + timeout
    found: dict[int, DiscoveredDevice] = {}
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock.settimeout(remaining)
        try:
            data, sender = sock.recvfrom(512)
        except TimeoutError:
            break
        ip = sender[0]
        device = _parse_reply(data, ip)
        if device is None:
            continue
        if device_type is not None and device.device_type != device_type:
            log.debug("ignoring device type 0x%02x at %s", device.device_type, ip)
            continue
        if device.device_id in found:
            continue
        found[device.device_id] = device
        log.info(
            "discovered id=%s type=0x%02x protocol=%d at %s:%d",
            device.device_id,
            device.device_type,
            device.protocol,
            device.ip,
            device.port,
        )
    return list(found.values())


def _parse_reply(data: bytes, ip: str) -> DiscoveredDevice | None:
    """Parse one datagram, or return None when it is not a V2/V3 reply."""
    packet, protocol = _unwrap(data)
    if packet is None or protocol is None:
        log.debug("ignoring discovery datagram from %s (%d bytes)", ip, len(data))
        return None
    if len(packet) < _MIN_V2_LENGTH:
        log.debug("ignoring short discovery reply from %s", ip)
        return None
    encrypted = packet[_V2_HEADER_LEN:-_V2_TRAILER_LEN]
    if len(encrypted) % _AES_BLOCK != 0:
        log.debug("ignoring unaligned discovery payload from %s", ip)
        return None
    try:
        device_id = int.from_bytes(packet[_DEVICE_ID_SLICE], "little")
        plain = pkcs7_unpad(aes_ecb_decrypt(encrypted, V2_ENC_KEY))
        device_type, port = _read_payload(plain, ip)
    except ValueError, UnicodeError:
        log.debug("ignoring undecodable discovery reply from %s", ip)
        return None
    if device_type is None or port is None:
        return None
    return DiscoveredDevice(
        ip=ip,
        device_id=device_id,
        device_type=device_type,
        protocol=protocol,
        port=port,
    )


def _unwrap(data: bytes) -> tuple[bytes | None, int | None]:
    """Return the inner 5A5A packet and the protocol version, if either is present."""
    if data[:2] == _V2_MAGIC:
        return data, 2
    if data[:2] == _V3_MAGIC and data[_V3_HEADER_LEN : _V3_HEADER_LEN + 2] == _V2_MAGIC:
        return data[_V3_HEADER_LEN:-_V2_TRAILER_LEN], 3
    return None, None


def _read_payload(plain: bytes, ip: str) -> tuple[int | None, int | None]:
    """Return the appliance type and TCP port stored in a decrypted payload."""
    if len(plain) <= _SSID_OFFSET:
        log.debug("ignoring discovery payload from %s: too short to carry an SSID", ip)
        return None, None
    ssid_len = plain[_SSID_LENGTH_OFFSET]
    ssid_end = _SSID_OFFSET + ssid_len
    if ssid_len == 0 or ssid_end > len(plain):
        log.debug("ignoring discovery payload from %s: SSID length %d", ip, ssid_len)
        return None, None
    ssid = plain[_SSID_OFFSET:ssid_end].decode("ascii")
    parts = ssid.split("_")
    if "_" not in ssid:
        log.debug("ignoring discovery payload from %s: SSID has no type", ip)
        return None, None
    device_type = int(parts[1], 16)
    port = int.from_bytes(plain[4:8], "little")
    reported = ".".join(str(octet) for octet in plain[3::-1])
    if reported != ip:
        log.debug("discovery payload IP %s differs from source %s", reported, ip)
    return device_type, port
