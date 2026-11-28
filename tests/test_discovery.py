"""LAN discovery tests. Replies are built the way a device answers, with no network."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, TypedDict

import pytest

from midea_dishwasher_api.discovery import DiscoveredDevice, DiscoveryError, discover
from midea_dishwasher_api.discovery.lan import _DISCOVERY_MESSAGE, _LISTEN_ADDRESS
from midea_dishwasher_api.security.crypto import V2_ENC_KEY, aes_ecb_encrypt, pkcs7_pad

if TYPE_CHECKING:
    from collections.abc import Iterator

# Published msmart-ng discovery reply from 10.100.1.140 (an AC, type 0xAC).
_PUBLISHED_V2 = bytes.fromhex(
    "5a5a011178007a8000000000000000000000000060ca0000000e0000000000000000000001000000"
    "c08651cb1b88a167bdcf7d37534ef81312d39429bf9b2673f200b635fae369a560fa9655eab8344b"
    "e22b1e3b024ef5dfd392dc3db64dbffb6a66fb9cd5ec87a78000cd9043833b9f76991e8af29f3496"
)
_PUBLISHED_ID = 15393162840672
_PUBLISHED_IP = "10.100.1.140"


class FakeUDP:
    """Datagram socket that records probes and serves a scripted reply queue."""

    def __init__(
        self,
        replies: list[tuple[bytes, tuple[str, int]]] | None = None,
        *,
        send_error: OSError | None = None,
        recv_error: OSError | None = None,
    ) -> None:
        self.replies: Iterator[tuple[bytes, tuple[str, int]]] = iter(replies or [])
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False
        self.bound: tuple[str, int] | None = None
        self.broadcast = False
        self._send_error = send_error
        self._recv_error = recv_error
        self.timeout: float | None = None

    def setsockopt(self, _level: int, name: int, value: int) -> None:
        if name == socket.SO_BROADCAST and value == 1:
            self.broadcast = True

    def bind(self, address: tuple[str, int]) -> None:
        self.bound = address

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        if self._send_error is not None:
            raise self._send_error
        self.sent.append((data, address))

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
        if self._recv_error is not None:
            raise self._recv_error
        try:
            return next(self.replies)
        except StopIteration:
            raise TimeoutError from None

    def close(self) -> None:
        self.closed = True


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeUDP) -> None:
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: fake)


class _Reply(TypedDict, total=False):
    ip: str
    port: int
    payload_ip: str
    ssid_len: int
    protocol: int


def _reply(device_id: int, ssid: str, extra: _Reply | None = None) -> tuple[bytes, tuple[str, int]]:
    """Build a V2 (or V3-wrapped) reply whose decrypted SSID is ``ssid``."""
    options: _Reply = {} if extra is None else extra
    ip = options.get("ip", "10.0.0.8")
    port = options.get("port", 6444)
    payload_ip = options.get("payload_ip")
    shown = ip if payload_ip is None else payload_ip
    octets = bytes(int(part) for part in shown.split("."))
    plain = bytearray(64)
    plain[0:4] = octets[::-1]
    plain[4:8] = port.to_bytes(4, "little")
    plain[8:40] = b"0" * 32
    encoded = ssid.encode("ascii")
    plain[40] = len(encoded) if "ssid_len" not in options else options["ssid_len"]
    plain[41 : 41 + len(encoded)] = encoded
    encrypted = aes_ecb_encrypt(pkcs7_pad(bytes(plain)), V2_ENC_KEY)
    header = bytearray(40)
    header[0:2] = b"\x5a\x5a"
    header[20:26] = device_id.to_bytes(6, "little")
    packet = bytes(header) + encrypted + b"\x00" * 16
    if options.get("protocol", 2) == 3:
        packet = b"\x83\x70" + b"\x00" * 6 + packet + b"\x00" * 16
    return packet, (ip, 6445)


def test_discover_parses_a_published_v2_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeUDP([(_PUBLISHED_V2, (_PUBLISHED_IP, 6445))])
    _install(monkeypatch, fake)

    found = discover(timeout=1, packets=1, address="255.255.255.255")

    assert found == [
        DiscoveredDevice(
            ip=_PUBLISHED_IP,
            device_id=_PUBLISHED_ID,
            device_type=0xAC,
            protocol=2,
            port=6444,
        )
    ]
    assert fake.broadcast is True
    assert fake.bound == _LISTEN_ADDRESS
    assert fake.closed is True
    assert fake.sent == [
        (_DISCOVERY_MESSAGE, ("255.255.255.255", 6445)),
        (_DISCOVERY_MESSAGE, ("255.255.255.255", 20086)),
    ]


def test_discover_parses_a_v3_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    packet, sender = _reply(99, "net_e1_abcd", {"protocol": 3})
    _install(monkeypatch, FakeUDP([(packet, sender)]))

    found = discover(timeout=1, packets=1, device_type=0xE1)

    assert found == [
        DiscoveredDevice(ip="10.0.0.8", device_id=99, device_type=0xE1, protocol=3, port=6444)
    ]


def test_default_probe_is_sent_three_times_on_both_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeUDP()
    _install(monkeypatch, fake)

    assert discover(timeout=0.01, address="192.168.1.255") == []
    ports = [address[1] for _data, address in fake.sent]
    assert ports == [6445, 6445, 6445, 20086, 20086, 20086]
    assert {address[0] for _data, address in fake.sent} == {"192.168.1.255"}


def test_device_type_filter_and_duplicate_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    first, sender = _reply(7, "net_e1_aaaa")
    again, _sender = _reply(7, "net_e1_aaaa")
    other, other_sender = _reply(8, "net_ac_bbbb", {"ip": "10.0.0.9"})
    fake = FakeUDP([(first, sender), (again, sender), (other, other_sender)])
    _install(monkeypatch, fake)

    found = discover(timeout=1, packets=1, device_type=0xE1)

    assert [device.device_id for device in found] == [7]


def test_malformed_replies_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    good, sender = _reply(5, "net_e1_ok")
    short = b"\x5a\x5a" + b"\x00" * 30
    tiny_plain = aes_ecb_encrypt(b"\x00" * 16, V2_ENC_KEY)
    tiny = b"\x5a\x5a" + b"\x00" * 38 + tiny_plain + b"\x00" * 16
    unaligned = b"\x5a\x5a" + b"\x00" * (40 - 2) + b"\x11" * 17 + b"\x00" * 16
    not_v3 = b"\x83\x70" + b"\x00" * 20
    zero_ssid, zero_sender = _reply(1, "", {"ssid_len": 0})
    long_ssid, long_sender = _reply(2, "net_e1_x", {"ssid_len": 200})
    no_type, no_type_sender = _reply(3, "notype")
    bad_hex, bad_hex_sender = _reply(4, "net_zz_x")
    replies: list[tuple[bytes, tuple[str, int]]] = [
        (b"not-a-discovery-reply", ("10.0.0.1", 1)),
        (short, ("10.0.0.2", 1)),
        (tiny, ("10.0.0.2", 1)),
        (unaligned, ("10.0.0.3", 1)),
        (not_v3, ("10.0.0.4", 1)),
        (zero_ssid, zero_sender),
        (long_ssid, long_sender),
        (no_type, no_type_sender),
        (bad_hex, bad_hex_sender),
        (good, sender),
    ]
    _install(monkeypatch, FakeUDP(replies))

    found = discover(timeout=1, packets=1)

    assert [device.device_id for device in found] == [5]


def test_non_ascii_ssid_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = bytearray(64)
    plain[0:4] = bytes((8, 0, 0, 10))
    plain[4:8] = (6444).to_bytes(4, "little")
    plain[40] = 1
    plain[41] = 0xFF
    encrypted = aes_ecb_encrypt(pkcs7_pad(bytes(plain)), V2_ENC_KEY)
    header = bytearray(40)
    header[0:2] = b"\x5a\x5a"
    header[20:26] = (11).to_bytes(6, "little")
    packet = bytes(header) + encrypted + b"\x00" * 16
    _install(monkeypatch, FakeUDP([(packet, ("10.0.0.8", 1))]))

    assert discover(timeout=1, packets=1) == []


def test_source_ip_wins_when_the_payload_disagrees(monkeypatch: pytest.MonkeyPatch) -> None:
    packet, _sender = _reply(15, "net_e1_abcd", {"ip": "192.168.5.9", "payload_ip": "10.0.0.8"})
    _install(monkeypatch, FakeUDP([(packet, ("192.168.5.9", 6445))]))

    found = discover(timeout=1, packets=1)

    assert found[0].ip == "192.168.5.9"
    assert found[0].port == 6444


def test_collect_stops_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    packet, sender = _reply(21, "net_e1_abcd")
    fake = FakeUDP([(packet, sender), (packet, sender)])
    _install(monkeypatch, fake)
    times = iter((0.0, 0.1, 50.0))
    monkeypatch.setattr(
        "midea_dishwasher_api.discovery.lan.time.monotonic",
        lambda: next(times),
    )

    found = discover(timeout=5, packets=1)

    assert len(found) == 1
    assert fake.timeout == pytest.approx(4.9)


def test_send_error_raises_discovery_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeUDP(send_error=OSError(1, "network unreachable"))
    _install(monkeypatch, fake)

    with pytest.raises(DiscoveryError, match="Failed to discover devices") as excinfo:
        discover(timeout=1, packets=1)
    assert isinstance(excinfo.value.__cause__, OSError)
    assert fake.closed is True


def test_recv_error_raises_discovery_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeUDP(recv_error=OSError(101, "network down"))
    _install(monkeypatch, fake)

    with pytest.raises(DiscoveryError, match="network down"):
        discover(timeout=1, packets=1)
    assert fake.closed is True


def test_socket_open_failure_raises_discovery_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> FakeUDP:
        raise OSError(13, "permission denied")

    monkeypatch.setattr(socket, "socket", refuse)

    with pytest.raises(DiscoveryError, match="permission denied"):
        discover(timeout=1)


def test_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        discover(timeout=0)
    with pytest.raises(ValueError, match="packets must be at least 1"):
        discover(packets=0)
