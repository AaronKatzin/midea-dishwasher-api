"""Async LAN V3 transport tests, driven by fake asyncio streams."""

from __future__ import annotations

import asyncio
import itertools
import os
from hashlib import sha256

import pytest

from midea_dishwasher_api.async_client import AsyncClient
from midea_dishwasher_api.protocol import assemble_frame, build_control, build_query
from midea_dishwasher_api.security import (
    PACKET_HEADER_LEN,
    SIGN_LEN,
    Security,
    aes_cbc_encrypt,
    v2_pack,
)
from midea_dishwasher_api.security.v3_error import V3Error
from midea_dishwasher_api.transport.async_v3_transport import AsyncV3Transport

DEVICE_ID = 151732606394621
TOKEN = os.urandom(64)
QUERY_AA = build_query()
CONTROL_AA = build_control({"machine_state": "power_off"})
KEY = bytes.fromhex("ab02a6952e5647c5bf0253d2b06f8e68687a2a2b5b3641e4bfee81d3f6291475")


class FakeReader:
    """Serves inbound bytes, optionally one at a time, or fails on demand."""

    def __init__(
        self,
        inbound: bytes,
        *,
        chunk: int | None = None,
        error: BaseException | None = None,
        empty_is_timeout: bool = False,
    ) -> None:
        self._inbound = bytearray(inbound)
        self._chunk = chunk
        self._error = error
        self._empty_is_timeout = empty_is_timeout

    async def read(self, size: int) -> bytes:
        if self._error is not None:
            raise self._error
        if not self._inbound:
            if self._empty_is_timeout:
                raise TimeoutError
            return b""
        take = size if self._chunk is None else min(size, self._chunk)
        chunk = bytes(self._inbound[:take])
        del self._inbound[:take]
        return chunk


class FakeWriter:
    """Records writes and can fail them."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.sent = bytearray()
        self.closed = False
        self._error = error

    def write(self, data: bytes) -> None:
        if self._error is not None:
            raise self._error
        self.sent.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _handshake_response(plain: bytes) -> bytes:
    """8370 type=1 with an AES(plain)+sha256(plain) payload, like the device sends."""
    body = aes_cbc_encrypt(plain, KEY, b"\x00" * 16) + sha256(plain).digest()
    return b"\x83\x70" + (64).to_bytes(2, "big") + b"\x20\x01" + b"\x00\x00" + body


def _enc_response(sec: Security, aa_frame: bytes) -> bytes:
    """Build a valid ENC_RESP (type=3) carrying `aa_frame` inside a V2 packet."""
    from midea_dishwasher_api.security import TYPE_ENCRYPTED_RESPONSE, aes_cbc_decrypt

    v2 = v2_pack(DEVICE_ID, aa_frame)
    pkt = bytearray(sec.encode(v2))
    pad = (pkt[5] >> 4) & 0xF
    pkt[5] = (pad << 4) | TYPE_ENCRYPTED_RESPONSE
    assert sec.tcp_key is not None
    plaintext = aes_cbc_decrypt(bytes(pkt[PACKET_HEADER_LEN:-SIGN_LEN]), sec.tcp_key, b"\x00" * 16)
    pkt[-SIGN_LEN:] = sha256(bytes(pkt[:PACKET_HEADER_LEN]) + plaintext).digest()
    return bytes(pkt)


def _status_aa(msg_type: int = 0x03) -> bytes:
    body = bytearray(46)
    body[0] = 0x08
    body[1] = 0x03
    return assemble_frame(bytes(body), msg_type)


def _patch_connection(
    monkeypatch: pytest.MonkeyPatch,
    reader: FakeReader,
    writer: FakeWriter,
) -> list[object]:
    """Make asyncio.open_connection return the fake streams and record the args."""
    calls: list[object] = []

    async def fake_open(host: str, port: int) -> tuple[FakeReader, FakeWriter]:
        calls.append((host, port))
        return reader, writer

    monkeypatch.setattr(
        "midea_dishwasher_api.transport.async_v3_transport.asyncio.open_connection",
        fake_open,
    )
    return calls


def _session_bytes(*frames: bytes) -> bytes:
    """Handshake response plus one ENC_RESP per application frame."""
    plain = os.urandom(32)
    sec = Security()
    sec.authenticate(_handshake_response(plain), KEY)
    return _handshake_response(plain) + b"".join(_enc_response(sec, frame) for frame in frames)


async def _connected(
    monkeypatch: pytest.MonkeyPatch,
    inbound: bytes,
    *,
    chunk: int | None = None,
    empty_is_timeout: bool = False,
    writer_error: BaseException | None = None,
) -> tuple[AsyncV3Transport, FakeReader, FakeWriter]:
    reader = FakeReader(inbound, chunk=chunk, empty_is_timeout=empty_is_timeout)
    writer = FakeWriter(error=writer_error)
    _patch_connection(monkeypatch, reader, writer)
    transport = AsyncV3Transport("10.0.0.5", DEVICE_ID, TOKEN, KEY, timeout=3.0)
    await transport.connect()
    return transport, reader, writer


def test_init_rejects_bad_token_length() -> None:
    with pytest.raises(ValueError, match="token must be 64 bytes"):
        AsyncV3Transport("1.2.3.4", DEVICE_ID, os.urandom(63), KEY)


def test_init_rejects_bad_key_length() -> None:
    with pytest.raises(ValueError, match="key must be 32 bytes"):
        AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, os.urandom(31))


@pytest.mark.asyncio
async def test_call_before_connect_raises() -> None:
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    with pytest.raises(V3Error, match="not connected"):
        await transport(b"\xaa\x0b\xe1")


@pytest.mark.asyncio
async def test_send_after_close_raises() -> None:
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    with pytest.raises(V3Error, match="not connected"):
        await transport._send_all(b"\x00")


@pytest.mark.asyncio
async def test_read_before_connect_raises() -> None:
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    with pytest.raises(V3Error, match="not connected"):
        await transport._read_exact(1, deadline=0)


@pytest.mark.asyncio
async def test_connect_handshake_and_request_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = FakeReader(_session_bytes(_status_aa()))
    writer = FakeWriter()
    calls = _patch_connection(monkeypatch, reader, writer)
    seen: list[tuple[str, int]] = []
    transport = AsyncV3Transport(
        "10.0.0.5",
        DEVICE_ID,
        TOKEN,
        KEY,
        port=6444,
        timeout=3.0,
        on_wire=lambda direction, data: seen.append((direction, len(data))),
    )
    await transport.connect()

    assert calls == [("10.0.0.5", 6444)]
    assert writer.sent[:2] == b"\x83\x70"
    assert await transport(QUERY_AA) == _status_aa()
    assert [direction for direction, _length in seen] == ["TX", "RX", "TX", "RX"]


@pytest.mark.asyncio
async def test_context_manager_connects_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = FakeReader(_session_bytes(_status_aa()))
    writer = FakeWriter()
    _patch_connection(monkeypatch, reader, writer)

    async with AsyncV3Transport("10.0.0.5", DEVICE_ID, TOKEN, KEY) as transport:
        assert await transport(QUERY_AA) == _status_aa()
    assert writer.closed is True


@pytest.mark.asyncio
async def test_close_is_idempotent_and_swallows_close_errors() -> None:
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    await transport.close()
    await transport.close()

    class BrokenWriter(FakeWriter):
        async def wait_closed(self) -> None:
            raise ConnectionError(104, "reset")

    transport._writer = BrokenWriter()  # type: ignore[assignment]
    await transport.close()
    assert transport._writer is None


@pytest.mark.asyncio
async def test_handshake_wrong_response_type_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from midea_dishwasher_api.security import TYPE_ERROR

    wrong = (
        b"\x83\x70"
        + (3).to_bytes(2, "big")
        + b"\x20"
        + bytes([TYPE_ERROR])
        + b"\x00\x00"
        + b"\x01\x02\x03"
    )
    _patch_connection(monkeypatch, FakeReader(wrong), FakeWriter())
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    with pytest.raises(V3Error, match="expected type=1"):
        await transport.connect()


@pytest.mark.asyncio
async def test_read_packet_bad_magic_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = b"\x99\x99" + (64).to_bytes(2, "big") + b"\x20\x01"
    _patch_connection(monkeypatch, FakeReader(bad), FakeWriter())
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    with pytest.raises(V3Error, match="bad magic"):
        await transport.connect()


@pytest.mark.asyncio
async def test_read_exact_eof_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_connection(monkeypatch, FakeReader(b"\x83\x70\x00"), FakeWriter())
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    with pytest.raises(V3Error, match="closed after"):
        await transport.connect()


@pytest.mark.asyncio
async def test_call_skips_non_enc_resp_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = os.urandom(32)
    sec = Security()
    sec.authenticate(_handshake_response(plain), KEY)
    noise = _handshake_response(os.urandom(32))
    inbound = _handshake_response(plain) + noise + _enc_response(sec, _status_aa())
    transport, _reader, _writer = await _connected(monkeypatch, inbound)
    assert await transport(QUERY_AA) == _status_aa()


@pytest.mark.asyncio
async def test_read_exact_reassembles_fragmented_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _reader, _writer = await _connected(
        monkeypatch,
        _session_bytes(_status_aa()),
        chunk=1,
    )
    assert await transport(QUERY_AA) == _status_aa()


@pytest.mark.asyncio
async def test_connect_refused_raises_v3error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(_host: str, _port: int) -> tuple[FakeReader, FakeWriter]:
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(
        "midea_dishwasher_api.transport.async_v3_transport.asyncio.open_connection",
        refuse,
    )
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    with pytest.raises(V3Error, match=r"Failed to connect to 1\.2\.3\.4:6444") as excinfo:
        await transport.connect()
    assert isinstance(excinfo.value.__cause__, ConnectionRefusedError)


@pytest.mark.asyncio
async def test_connect_timeout_raises_v3error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def time_out(_host: str, _port: int) -> tuple[FakeReader, FakeWriter]:
        raise TimeoutError

    monkeypatch.setattr(
        "midea_dishwasher_api.transport.async_v3_transport.asyncio.open_connection",
        time_out,
    )
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    with pytest.raises(V3Error, match="Failed to connect") as excinfo:
        await transport.connect()
    assert isinstance(excinfo.value.__cause__, TimeoutError)


@pytest.mark.asyncio
async def test_send_socket_error_raises_v3error(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = FakeReader(_session_bytes())
    writer = FakeWriter()
    _patch_connection(monkeypatch, reader, writer)
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    await transport.connect()
    writer._error = BrokenPipeError(32, "Broken pipe")
    with pytest.raises(V3Error, match="Failed to send request") as excinfo:
        await transport._send_all(b"\x00")
    assert isinstance(excinfo.value.__cause__, BrokenPipeError)


@pytest.mark.asyncio
async def test_read_socket_error_raises_v3error(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = FakeReader(b"", error=ConnectionResetError(104, "Connection reset by peer"))
    _patch_connection(monkeypatch, reader, FakeWriter())
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    with pytest.raises(V3Error, match="Failed to read response") as excinfo:
        await transport.connect()
    assert isinstance(excinfo.value.__cause__, ConnectionResetError)


@pytest.mark.asyncio
async def test_read_timeout_raises_v3error(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = FakeReader(b"", error=TimeoutError())
    _patch_connection(monkeypatch, reader, FakeWriter())
    transport = AsyncV3Transport("1.2.3.4", DEVICE_ID, TOKEN, KEY)
    with pytest.raises(V3Error, match="no matching reply in time") as excinfo:
        await transport.connect()
    assert isinstance(excinfo.value.__cause__, TimeoutError)


@pytest.mark.asyncio
async def test_query_skips_the_connect_push(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = _status_aa(0x03)
    transport, _reader, _writer = await _connected(
        monkeypatch, _session_bytes(_status_aa(0x04), reply)
    )
    assert await transport(QUERY_AA) == reply


@pytest.mark.asyncio
async def test_query_falls_back_to_the_push_when_the_reply_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    push = _status_aa(0x04)
    timeouts: list[float] = []
    real_wait_for = asyncio.wait_for

    async def recording_wait_for(awaitable: object, wait: float) -> object:
        timeouts.append(wait)
        return await real_wait_for(awaitable, wait)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "midea_dishwasher_api.transport.async_v3_transport.asyncio.wait_for",
        recording_wait_for,
    )
    transport, _reader, _writer = await _connected(
        monkeypatch,
        _session_bytes(push),
        empty_is_timeout=True,
    )
    before = len(timeouts)
    assert await transport(QUERY_AA) == push
    assert timeouts[before:]
    assert timeouts[-1] <= 1.0


@pytest.mark.asyncio
async def test_query_without_any_status_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _reader, _writer = await _connected(
        monkeypatch, _session_bytes(), empty_is_timeout=True
    )
    with pytest.raises(V3Error, match="no matching reply in time") as excinfo:
        await transport(QUERY_AA)
    assert isinstance(excinfo.value.__cause__, TimeoutError)


@pytest.mark.asyncio
async def test_control_does_not_take_the_push_as_its_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport, _reader, _writer = await _connected(
        monkeypatch,
        _session_bytes(_status_aa(0x04)),
        empty_is_timeout=True,
    )
    with pytest.raises(V3Error, match="no matching reply in time"):
        await transport(CONTROL_AA)


@pytest.mark.asyncio
async def test_query_skips_frames_that_carry_no_status(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = _status_aa(0x03)
    transport, _reader, _writer = await _connected(
        monkeypatch, _session_bytes(_status_aa(0x02), reply)
    )
    assert await transport(QUERY_AA) == reply


@pytest.mark.asyncio
async def test_control_waits_for_its_ack_past_pushes(monkeypatch: pytest.MonkeyPatch) -> None:
    ack = _status_aa(0x02)
    transport, reader, _writer = await _connected(
        monkeypatch,
        _session_bytes(_status_aa(0x04), ack, _status_aa(0x04)),
    )
    assert await transport(CONTROL_AA) == ack
    assert await reader.read(1) != b""


@pytest.mark.asyncio
async def test_reply_without_aa_header_is_returned_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _reader, _writer = await _connected(monkeypatch, _session_bytes(b"\x00\x01\x02"))
    assert await transport(QUERY_AA) == b"\x00\x01\x02"


@pytest.mark.asyncio
async def test_non_aa_request_returns_first_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    push = _status_aa(0x04)
    transport, _reader, _writer = await _connected(
        monkeypatch, _session_bytes(push, _status_aa(0x03))
    )
    assert await transport(b"raw") == push


@pytest.mark.asyncio
async def test_wait_for_reply_is_bounded_by_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _reader, _writer = await _connected(
        monkeypatch, _session_bytes(*([_status_aa(0x04)] * 5))
    )
    clock = itertools.count(start=0.0, step=4.0)
    monkeypatch.setattr(
        "midea_dishwasher_api.transport.async_v3_transport.time.monotonic",
        lambda: next(clock),
    )
    with pytest.raises(V3Error, match="no matching reply in time") as excinfo:
        await transport(CONTROL_AA)
    assert isinstance(excinfo.value.__cause__, TimeoutError)


@pytest.mark.asyncio
async def test_async_client_uses_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _reader, _writer = await _connected(
        monkeypatch, _session_bytes(_status_aa(), _status_aa(0x02))
    )
    client = AsyncClient(send=transport)
    status = await client.query_status()
    assert status.cycle_state is not None
    await client.power_off()


@pytest.mark.asyncio
async def test_async_client_methods_build_the_same_frames_as_the_sync_client() -> None:
    from midea_dishwasher_api import BrightLevel, Mode

    sent: list[bytes] = []

    async def capture(frame: bytes) -> bytes:
        sent.append(frame)
        return _status_aa()

    client = AsyncClient(send=capture)
    await client.power_on()
    await client.power_off()
    await client.cancel_work()
    await client.start_to_work(Mode.ECO, extra_drying=True)
    await client.set_bright(BrightLevel.L3)
    assert sent[0] == build_control({"machine_state": "power_on"})
    assert sent[1] == build_control({"machine_state": "power_off"})
    assert sent[2] == build_control({"machine_state": "cancel"})
    assert sent[3] == build_control({"mode": "eco", "machine_state": "work", "additional": 1})
    assert sent[4] == build_control({"bright": 3})
