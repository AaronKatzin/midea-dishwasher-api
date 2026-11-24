"""Python client for Midea dishwashers (device type `0xE1`)."""

from importlib.metadata import PackageNotFoundError, version

from .client import Client
from .cloud import (
    CloudError,
    DeviceCredentials,
    MSmartHomeCloud,
    NetHomePlusCloud,
    udp_id,
)
from .enums import (
    BrightLevel,
    CycleState,
    ErrorCode,
    MachineState,
    Mode,
    WashStage,
)
from .protocol import FrameError
from .security import V3Error
from .state import DishwasherStatus
from .transport import V3Transport

try:
    __version__ = version("midea-dishwasher-api")
except PackageNotFoundError:
    __version__ = "0.0.0+local"

__all__ = [
    "BrightLevel",
    "Client",
    "CloudError",
    "CycleState",
    "DeviceCredentials",
    "DishwasherStatus",
    "ErrorCode",
    "FrameError",
    "MSmartHomeCloud",
    "MachineState",
    "Mode",
    "NetHomePlusCloud",
    "V3Error",
    "V3Transport",
    "WashStage",
    "__version__",
    "udp_id",
]
