"""Asyncio counterpart of the LAN V3 transport."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Self

from ..enums import MsgType
from ..protocol import FrameError, parse_frame
from ..security import (
    KEY_LEN,
    MAGIC,
    PACKET_HEADER_LEN,
    PACKET_ID_LEN,
    TOKEN_LEN,
    TYPE_ENCRYPTED_RESPONSE,
    TYPE_HANDSHAKE_RESPONSE,
    Security,
    V3Error,
    v2_pack,
    v2_unpack,
)

if TYPE_CHECKING:
    from types import TracebackType

    from .v3_transport import OnWireCallback

log: logging.Logger = logging.getLogger("midea_dishwasher_api.async_transport")

_QUERY_REPLY_GRACE = 1.0


def _noop_on_wire(_direction: str, _data: bytes) -> None:
    """Drop wire traces when the caller asked for none."""
    return


def _aa_msg_type(frame: bytes) -> int | None:
    """Return the message type of an AA frame, or None when the frame is not one."""
    try:
        msg_type, _body = parse_frame(frame)
    except FrameError:
        return None
    return msg_type


class AsyncV3Transport:
    """
    An asyncio LAN V3 session, usable as an async context manager.

    An instance is callable, so it can be passed straight to
    ``AsyncClient(send=transport)``. ``await transport(frame)`` returns the
    answering application frame, the same contract as ``V3Transport``.
    """

    def __init__(  # noqa: PLR0913, PLR0917 — host plus credentials are one logical bundle.
        self,
        host: str,
        device_id: int,
        token: bytes,
        key: bytes,
        port: int = 6444,
        timeout: float = 10.0,
        on_wire: OnWireCallback | None = None,
    ) -> None:
        """Validate the credentials up front and prepare an unconnected session."""
        if len(token) != TOKEN_LEN:
            msg = f"Failed to build transport: token must be {TOKEN_LEN} bytes (got {len(token)})"
            raise ValueError(msg)
        if len(key) != KEY_LEN:
            msg = f"Failed to build transport: key must be {KEY_LEN} bytes (got {len(key)})"
            raise ValueError(msg)
        self.host: str = host
        self.port: int = port
        self.device_id: int = device_id
        self._token: bytes = token
        self._key: bytes = key
        self._timeout: float = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._security: Security = Security()
        self._on_wire: OnWireCallback = on_wire or _noop_on_wire

    async def __aenter__(self) -> Self:
        """Open the session on entry."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the session on exit."""
        await self.close()

    async def connect(self) -> None:
        """Open the TCP connection and complete the V3 handshake."""
        log.debug("connecting to %s:%d", self.host, self.port)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                self._timeout,
            )
        except (TimeoutError, OSError) as error:
            msg = f"Failed to connect to {self.host}:{self.port}: {error}"
            raise V3Error(msg) from error
        await self._handshake()

    async def close(self) -> None:
        """Close the TCP connection, if one is open."""
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            log.debug("socket close raised", exc_info=True)

    async def __call__(self, frame: bytes) -> bytes:
        """Send an application frame and return the frame that answers it."""
        if self._writer is None:
            msg = "Failed to send frame: transport not connected"
            raise V3Error(msg)
        v2 = v2_pack(self.device_id, frame)
        request = self._security.encode(v2)
        self._on_wire("TX", request)
        await self._send_all(request)
        return await self._await_answer(_aa_msg_type(frame))

    async def _await_answer(self, request_type: int | None) -> bytes:
        """Return the reply matching the request type, or the status push for a query."""
        # The machine pushes its status (0x04) unsolicited, even to a wrong
        # device_id, and drops some 0x03 query replies.
        deadline = time.monotonic() + self._timeout
        push: bytes | None = None
        while True:
            try:
                reply = await self._recv_reply(deadline)
            except V3Error as error:
                if push is not None and isinstance(error.__cause__, TimeoutError):
                    log.debug("no reply to the query, answering with the status push")
                    return push
                raise
            reply_type = _aa_msg_type(reply)
            if request_type is None or reply_type in {request_type, None}:
                return reply
            if request_type == MsgType.QUERY and reply_type == MsgType.PUSH:
                if push is None:
                    deadline = min(deadline, time.monotonic() + _QUERY_REPLY_GRACE)
                push = reply
                continue
            log.debug(
                "ignoring AA frame of type 0x%02x while waiting for type 0x%02x",
                reply_type,
                request_type,
            )

    async def _recv_reply(self, deadline: float) -> bytes:
        """Return the next AA frame the device sends, failing once the deadline passes."""
        while True:
            packet = await self._read_packet(deadline)
            self._on_wire("RX", packet)
            msg_type, v2_body = self._security.decode(packet)
            if msg_type == TYPE_ENCRYPTED_RESPONSE:
                return v2_unpack(v2_body)
            log.debug("ignoring frame with msg_type=0x%x while waiting for ENC_RESP", msg_type)

    async def _handshake(self) -> None:
        """Exchange the handshake and derive the session key."""
        request = self._security.handshake_request(self._token)
        self._on_wire("TX", request)
        await self._send_all(request)

        packet = await self._read_packet(time.monotonic() + self._timeout)
        self._on_wire("RX", packet)
        msg_type, _body = self._security.decode(packet)
        if msg_type != TYPE_HANDSHAKE_RESPONSE:
            msg = f"Failed to handshake: expected type=1, got type=0x{msg_type:x}"
            raise V3Error(msg)
        self._security.authenticate(packet, self._key)
        log.info("V3 session established with %s", self.host)

    async def _send_all(self, data: bytes) -> None:
        """Write every byte to the socket."""
        writer = self._connected_writer()
        try:
            writer.write(data)
            await writer.drain()
        except OSError as error:
            msg = f"Failed to send request: {error}"
            raise V3Error(msg) from error

    async def _read_packet(self, deadline: float) -> bytes:
        """Read one whole 8370 packet off the socket."""
        head = await self._read_exact(PACKET_HEADER_LEN, deadline)
        if head[:2] != MAGIC:
            msg = f"Failed to read response: bad magic {head[:2].hex()}"
            raise V3Error(msg)
        size = (head[2] << 8) | head[3]
        body = await self._read_exact(PACKET_ID_LEN + size, deadline)
        return head + body

    async def _read_exact(self, size: int, deadline: float) -> bytes:
        """Read exactly ``size`` bytes, or fail if the device hangs up first."""
        reader = self._connected_reader()
        buffer = bytearray()
        while len(buffer) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                msg = "Failed to read response: no matching reply in time"
                raise V3Error(msg) from TimeoutError()
            try:
                chunk = await asyncio.wait_for(reader.read(size - len(buffer)), remaining)
            except TimeoutError as error:
                msg = "Failed to read response: no matching reply in time"
                raise V3Error(msg) from error
            except OSError as error:
                msg = f"Failed to read response: {error}"
                raise V3Error(msg) from error
            if not chunk:
                msg = f"Failed to read response: closed after {len(buffer)}/{size} bytes"
                raise V3Error(msg)
            buffer.extend(chunk)
        return bytes(buffer)

    def _connected_reader(self) -> asyncio.StreamReader:
        """Return the open reader, or fail when the session is not connected."""
        if self._reader is None:
            msg = "Failed to use transport: not connected"
            raise V3Error(msg)
        return self._reader

    def _connected_writer(self) -> asyncio.StreamWriter:
        """Return the open writer, or fail when the session is not connected."""
        if self._writer is None:
            msg = "Failed to use transport: not connected"
            raise V3Error(msg)
        return self._writer
