"""High-level client: one method per operation the vendor app performs."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .enums import BrightLevel
from .protocol import build_control, build_query
from .state import decode_response

if TYPE_CHECKING:
    from .enums import Mode
    from .protocol import ControlPayload
    from .state import DishwasherStatus

Send = Callable[[bytes], bytes]


class Client:
    """Talks the application protocol over any request/response transport."""

    def __init__(self, send: Send) -> None:
        """Bind the client to the transport that carries its frames."""
        self._send: Send = send

    def query_status(self) -> DishwasherStatus:
        """Return the machine's current status."""
        return decode_response(self._send(build_query()))

    def power_on(self) -> None:
        """Power the machine on."""
        self._control({"machine_state": "power_on"})

    def power_off(self) -> None:
        """Power the machine off."""
        self._control({"machine_state": "power_off"})

    def cancel_work(self) -> None:
        """Cancel the running cycle and return the machine to idle."""
        self._control({"machine_state": "cancel"})

    def start_to_work(
        self,
        mode: Mode,
        *,
        extra_drying: bool = False,
        door_auto_open: bool | None = None,
    ) -> None:
        """
        Start a wash cycle with the given program.

        ``door_auto_open`` left as None keeps the machine's current setting.
        ``True`` enables auto-open and ``False`` disables it.
        """
        self._control(
            {
                "mode": str(mode),
                "machine_state": "work",
                "additional": 1 if extra_drying else 0,
                **_door_auto_open_field(door_auto_open=door_auto_open),
            }
        )

    def pause_work(self) -> None:
        """Pause the running cycle."""
        self._control({"operator": "pause"})

    def resume_work(self) -> None:
        """Resume a paused cycle."""
        self._control({"operator": "resume"})

    def set_delayed_start(
        self,
        mode: Mode,
        minutes: int,
        *,
        extra_drying: bool = False,
        door_auto_open: bool | None = None,
    ) -> None:
        """
        Schedule ``mode`` to start after ``minutes`` (1 to 1440).

        A pending delay is not cancelled by ``cancel_work``; use
        ``cancel_delayed_start``. ``door_auto_open`` follows the same
        tri-state contract as ``start_to_work``.
        """
        self._control(
            {
                "mode": str(mode),
                "machine_state": "order",
                "additional": 1 if extra_drying else 0,
                "delay_minutes": minutes,
                **_door_auto_open_field(door_auto_open=door_auto_open),
            }
        )

    def cancel_delayed_start(self, mode: Mode) -> None:
        """
        Cancel a pending delayed start of ``mode``.

        The program byte is sent because the captures that confirmed this
        command included the scheduled program. ``cancel_work`` uses a
        different status byte and leaves a delay running.
        """
        self._control({"machine_state": "cancel_order", "mode": str(mode)})

    def set_bright(self, level: BrightLevel) -> None:
        """Set the rinse-aid dosage level, rejecting levels outside 1-5."""
        self._control({"bright": int(BrightLevel(level))})

    def set_child_lock(self, *, enabled: bool) -> None:
        """Enable or disable the child lock."""
        self._control({"child_lock": enabled})

    def _control(self, payload: ControlPayload) -> None:
        """Send a control frame and drop the acknowledgement."""
        self._send(build_control(payload))


def _door_auto_open_field(*, door_auto_open: bool | None) -> ControlPayload:
    """Return the door auto-open field, or nothing when it stays unchanged."""
    if door_auto_open is None:
        return {}
    return {"door_auto_open": "enable" if door_auto_open else "disable"}
