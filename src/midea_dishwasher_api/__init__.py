"""Python client for Midea dishwashers (device type `0xE1`)."""

from importlib.metadata import PackageNotFoundError, version

from .async_client import AsyncClient
from .client import Client
from .enums import (
    BrightLevel,
    CycleState,
    ErrorCode,
    MachineState,
    Mode,
    Operator,
    WashStage,
)
from .protocol import FrameError
from .security import V3Error
from .state import DishwasherStatus
from .transport import AsyncV3Transport, V3Transport

try:
    __version__ = version("midea-dishwasher-api")
except PackageNotFoundError:
    __version__ = "0.0.0+local"

__all__ = [
    "AsyncClient",
    "AsyncV3Transport",
    "BrightLevel",
    "Client",
    "CycleState",
    "DishwasherStatus",
    "ErrorCode",
    "FrameError",
    "MachineState",
    "Mode",
    "Operator",
    "V3Error",
    "V3Transport",
    "WashStage",
    "__version__",
]
