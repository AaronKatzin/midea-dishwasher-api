"""Run/pause flag while a cycle is scheduled or running (byte 5, bit 0x08)."""

from __future__ import annotations

from enum import StrEnum


class Operator(StrEnum):
    """
    Whether the machine reports the cycle as started or paused.

    Midea's status mapping calls this the operator: the start bit set means
    ``start``; the bit clear while the cycle is ``order`` or ``work`` means
    ``pause``. A delayed start therefore reports ``pause`` until it begins.
    """

    START = "start"
    PAUSE = "pause"
