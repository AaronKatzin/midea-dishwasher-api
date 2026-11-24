"""Asyncio counterpart of the high-level dishwasher client."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .enums import BrightLevel
from .protocol import build_control, build_query
from .state import decode_response

if TYPE_CHECKING:
    from .enums import Mode
    from .protocol import ControlPayload
    from .state import DishwasherStatus

AsyncSend = Callable[[bytes], Awaitable[bytes]]


class AsyncClient:
    """Talks the application protocol over any async request/response transport."""

    def __init__(self, send: AsyncSend) -> None:
        """Bind the client to the transport that carries its frames."""
        self._send: AsyncSend = send

    async def query_status(self) -> DishwasherStatus:
        """Return the machine's current status."""
        return decode_response(await self._send(build_query()))

    async def power_on(self) -> None:
        """Power the machine on."""
        await self._control({"machine_state": "power_on"})

    async def power_off(self) -> None:
        """Power the machine off."""
        await self._control({"machine_state": "power_off"})

    async def cancel_work(self) -> None:
        """Cancel the running cycle and return the machine to idle."""
        await self._control({"machine_state": "cancel"})

    async def start_to_work(self, mode: Mode, *, extra_drying: bool = False) -> None:
        """Start a wash cycle with the given program."""
        await self._control(
            {
                "mode": str(mode),
                "machine_state": "work",
                "additional": 1 if extra_drying else 0,
            }
        )

    async def set_bright(self, level: BrightLevel) -> None:
        """Set the rinse-aid dosage level, rejecting levels outside 1-5."""
        await self._control({"bright": int(BrightLevel(level))})

    async def _control(self, payload: ControlPayload) -> None:
        """Send a control frame and drop the acknowledgement."""
        await self._send(build_control(payload))
