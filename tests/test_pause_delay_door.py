"""Pause, delayed start and door auto-open frames, plus the status fields they report."""

from __future__ import annotations

from typing import cast

import pytest

from midea_dishwasher_api import Client, CycleState, Mode, Operator
from midea_dishwasher_api.protocol import ControlPayload, assemble_frame, build_control
from midea_dishwasher_api.state import decode_response

_BODY_HEX_LEN = 76


def _body(frame: bytes) -> bytes:
    return frame[10:48]


def _padded(prefix: str) -> bytes:
    """Return a 38-byte control body from a hex prefix, zero-padded."""
    return bytes.fromhex(prefix.ljust(_BODY_HEX_LEN, "0"))


def _ack(_: bytes) -> bytes:
    body = bytearray(46)
    body[0] = 0x08
    return assemble_frame(bytes(body), 0x02)


def test_pause_frame_is_operator_command() -> None:
    frame = build_control({"operator": "pause"})
    assert _body(frame) == _padded("8302")
    assert len(frame) == 49


def test_resume_frame_is_operator_command() -> None:
    frame = build_control({"operator": "resume"})
    assert _body(frame) == _padded("8301")


def test_delayed_start_eco_one_hour_with_door_auto_open() -> None:
    # Body verified on a 76000228: order, ECO, 1 hour, door auto-open enabled.
    frame = build_control(
        {
            "machine_state": "order",
            "mode": "eco",
            "delay_minutes": 60,
            "door_auto_open": "enable",
        }
    )
    assert _body(frame) == _padded("0802040000000001000000000001")


def test_delayed_start_splits_hours_and_minutes() -> None:
    frame = build_control({"machine_state": "order", "mode": "eco", "delay_minutes": 3 * 60 + 34})
    assert _body(frame) == _padded("080204000000000322")


def test_delayed_start_ninety_five_minutes_extra_drying_door_disabled() -> None:
    frame = build_control(
        {
            "machine_state": "order",
            "mode": "eco",
            "additional": 1,
            "delay_minutes": 95,
            "door_auto_open": "disable",
        }
    )
    assert _body(frame) == _padded("0802040100000001230000000002")


@pytest.mark.parametrize(
    ("minutes", "prefix"),
    [(1, "080204000000000001"), (24 * 60, "080204000000001800")],
)
def test_delayed_start_boundaries(minutes: int, prefix: str) -> None:
    frame = build_control({"machine_state": "order", "mode": "eco", "delay_minutes": minutes})
    assert _body(frame) == _padded(prefix)


def test_delay_bytes_are_not_written_unless_ordering() -> None:
    frame = build_control({"machine_state": "work", "mode": "eco", "delay_minutes": 60})
    body = _body(frame)
    assert body == _padded("080304")
    assert body[7] == 0x00
    assert body[8] == 0x00


def test_start_to_work_door_auto_open_enable_and_disable() -> None:
    enabled = build_control({"machine_state": "work", "mode": "eco", "door_auto_open": "enable"})
    disabled = build_control({"machine_state": "work", "mode": "eco", "door_auto_open": "disable"})
    assert _body(enabled) == _padded("0803040000000000000000000001")
    assert _body(disabled)[13] == 0x02
    assert _body(disabled)[:13] == _padded("080304")[:13]


def test_start_to_work_leaves_door_auto_open_unchanged() -> None:
    frame = build_control({"machine_state": "work", "mode": "eco"})
    assert _body(frame)[13] == 0x00


def test_cancel_delayed_start_sends_cancel_order_and_program() -> None:
    frame = build_control({"machine_state": "cancel_order", "mode": "eco"})
    assert _body(frame) == _padded("080404")


def test_unknown_operator_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown action"):
        build_control({"operator": "spin"})


def test_unknown_door_auto_open_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown setting"):
        build_control({"machine_state": "work", "mode": "eco", "door_auto_open": "maybe"})


def test_delayed_start_rejects_missing_mode() -> None:
    with pytest.raises(ValueError, match="no work mode selected"):
        build_control({"machine_state": "order", "delay_minutes": 60})


def test_delayed_start_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="no work mode selected"):
        build_control(
            {
                "machine_state": "order",
                "mode": "does-not-exist",
                "delay_minutes": 60,
            }
        )


@pytest.mark.parametrize("minutes", [None, 0, 24 * 60 + 1, True, False])
def test_delayed_start_rejects_delay_out_of_range(minutes: object) -> None:
    payload: dict[str, object] = {"machine_state": "order", "mode": "eco"}
    if minutes is not None:
        payload["delay_minutes"] = minutes
    with pytest.raises(ValueError, match="delay must be an integer"):
        build_control(cast("ControlPayload", payload))


def test_client_pause_resume_delay_and_door() -> None:
    captured: list[bytes] = []

    def fake_send(frame: bytes) -> bytes:
        captured.append(frame)
        return _ack(frame)

    client = Client(send=fake_send)
    client.pause_work()
    client.resume_work()
    client.set_delayed_start(Mode.ECO, 60, door_auto_open=True)
    client.set_delayed_start(Mode.ECO, 95, extra_drying=True, door_auto_open=False)
    client.cancel_delayed_start(Mode.ECO)
    client.start_to_work(Mode.ECO, door_auto_open=True)
    client.start_to_work(Mode.ECO)

    assert _body(captured[0]) == _padded("8302")
    assert _body(captured[1]) == _padded("8301")
    assert _body(captured[2]) == _padded("0802040000000001000000000001")
    assert _body(captured[3]) == _padded("0802040100000001230000000002")
    assert _body(captured[4]) == _padded("080404")
    assert _body(captured[5]) == _padded("0803040000000000000000000001")
    assert _body(captured[6])[13] == 0x00


def test_client_rejects_delay_out_of_range() -> None:
    client = Client(send=_ack)
    with pytest.raises(ValueError, match="delay must be an integer"):
        client.set_delayed_start(Mode.ECO, 0)


def _status_frame(body: bytes) -> bytes:
    return assemble_frame(body, 0x03)


def test_decode_delayed_start_status() -> None:
    body = bytearray(46)
    body[1] = 0x02  # ORDER
    body[2] = 0x04  # ECO
    body[4] = 0x40  # door auto-open
    body[5] = 0x00  # operator bit clear -> pause while waiting
    body[7] = 0
    body[8] = 59
    status = decode_response(_status_frame(bytes(body)))

    assert status.cycle_state == CycleState.ORDER
    assert status.mode == Mode.ECO
    assert status.delay_remaining == 59
    assert status.left_time is None
    assert status.door_auto_open is True
    assert status.operator == Operator.PAUSE


def test_decode_running_cycle_reports_start_and_ignores_delay_bytes() -> None:
    body = bytearray(46)
    body[1] = 0x03  # WORK
    body[4] = 0x00
    body[5] = 0x08  # operator start bit
    body[6] = 30
    body[7] = 1
    body[8] = 0
    status = decode_response(_status_frame(bytes(body)))

    assert status.cycle_state == CycleState.WORK
    assert status.operator == Operator.START
    assert status.left_time == 30
    assert status.delay_remaining is None
    assert status.door_auto_open is False


def test_decode_paused_work_cycle() -> None:
    body = bytearray(46)
    body[1] = 0x03
    body[5] = 0x01  # door closed, operator bit clear
    status = decode_response(_status_frame(bytes(body)))

    assert status.operator == Operator.PAUSE
    assert status.door_closed is True


def test_decode_idle_has_no_operator() -> None:
    body = bytearray(46)
    body[1] = 0x01  # IDLE
    body[5] = 0x08
    status = decode_response(_status_frame(bytes(body)))

    assert status.operator is None
    assert status.delay_remaining is None


def test_decode_order_without_delay_bytes_leaves_remaining_unset() -> None:
    body = bytearray(8)  # indexes 0..7: minute byte is absent
    body[1] = 0x02
    body[5] = 0x08
    status = decode_response(_status_frame(bytes(body)))

    assert status.cycle_state == CycleState.ORDER
    assert status.delay_remaining is None
    assert status.operator == Operator.START


def test_decode_work_without_operator_byte_leaves_operator_unset() -> None:
    body = bytearray(5)  # indexes 0..4
    body[1] = 0x03
    body[4] = 0x40
    status = decode_response(_status_frame(bytes(body)))

    assert status.cycle_state == CycleState.WORK
    assert status.operator is None
    assert status.door_auto_open is True
