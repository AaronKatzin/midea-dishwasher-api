# midea-dishwasher-api

[![CI](https://github.com/roquerodrigo/midea-dishwasher-api/actions/workflows/ci.yml/badge.svg)](https://github.com/roquerodrigo/midea-dishwasher-api/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/midea-dishwasher-api)](https://pypi.org/project/midea-dishwasher-api/)

[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-db61a2?logo=githubsponsors&logoColor=white&style=for-the-badge)](https://github.com/sponsors/roquerodrigo)

Python client for Midea dishwashers (`device_type 0xE1`, plugin v5).

Implements the `AA … E1` application protocol and the LAN V3 transport (8370
handshake + AES-128-CBC + SHA-256, with the inner V2 `5A5A` framing).

## Installation

```bash
pip install midea-dishwasher-api
```

## Quick start

```python
from midea_dishwasher_api import BrightLevel, Client, Mode, V3Transport

with V3Transport(
    host="192.168.5.100",
    device_id=151732606394621,
    token=bytes.fromhex("..."),  # 64 bytes
    key=bytes.fromhex("..."),    # 32 bytes
) as transport:
    client = Client(send=transport)

    status = client.query_status()
    print(status.machine_state)   # MachineState.POWER_ON / POWER_OFF
    print(status.cycle_state)     # CycleState.IDLE / WORK / ORDER / ...
    print(status.left_time)       # minutes remaining (only while WORK)
    print(status.door_closed)
    print(status.bright_lack)     # is the rinse aid empty?

    client.power_on()
    client.start_to_work(mode=Mode.ECO, extra_drying=True)
    client.set_bright(BrightLevel.L3)
    client.cancel_work()
    client.power_off()
```

Control methods return nothing: the machine takes a few seconds to reflect a
change. Call `query_status()` whenever fresh state is needed.

## API

### Client

| Method | Effect |
|---|---|
| `query_status() -> DishwasherStatus` | Read the current state |
| `power_on()` | Power the machine on |
| `power_off()` | Power it off |
| `cancel_work()` | Cancel the cycle / return to idle |
| `start_to_work(mode, extra_drying=False)` | Start a cycle |
| `set_bright(level: BrightLevel)` | Set the rinse-aid level (1–5) |
| `set_child_lock(*, enabled)` | Enable or disable the child lock |

`extra_drying` is keyword-only.

### DishwasherStatus

Fields decoded from a response:

- `machine_state: MachineState | None` — `POWER_ON` / `POWER_OFF`
- `cycle_state: CycleState | None` — `idle`, `order`, `work`, `error`, ...
- `mode: Mode | int | None` — the running program; `None` when there is none
  (`0x00`), `int` for program bytes not yet catalogued in the enum
- `extra_drying: bool` — "extra drying" flag of the current program
- `wash_stage: WashStage | int | None` — `IDLE`, `PRE_WASH`, `MAIN_WASH`, `RINSE`, `DRY`, `FINISH`
- `error_code: ErrorCode | int` — `NONE`, `WATER_SUPPLY`, `HEATING`, `OVERFLOW`, `WATER_VALVE`
- `left_time: int | None` — minutes remaining (only set when `cycle_state == WORK`)
- `door_closed: bool`
- `child_lock: bool` — bit 4 of the flag byte (`body[5] & 0x10`), the same bit midea-local reads
- `bright_lack: bool` — the rinse aid ran out
- `bright: BrightLevel | int | None` — current rinse-aid level (1–5)
- `softwater_lack: bool` — the salt for water softening ran out
- `temperature: int | None` — tub water temperature in °C

### Available programs (`Mode`)

`AUTO`, `INTENSIVE`, `NORMAL`, `ECO`, `GLASS`, `NINETY_MIN`, `ONE_HOUR`,
`RAPID`, `SOAK`, `THREE_IN_ONE`, `HYGIENE`, `QUIET`, `PARTY`, `FRUIT`.

## Child lock and half load

`set_child_lock(enabled=True)` sends opcode `0x83` with byte 1 set to `0x03` (lock) or
`0x04` (unlock), padded to the usual 38-byte control body. That layout is
`MessageLock` in [midea-local](https://github.com/midea-lan/midea-local/blob/master/midealocal/devices/e1/message.py)
and the E1 Lua quoted in
[midea_ac_lan#709](https://github.com/wuwentao/midea_ac_lan/issues/709).
Status uses the same flag byte this SDK already decodes for the door and the
rinse-aid and salt warnings: bit `0x10` is the child lock.

Half load is **not implemented**. Public sources do not agree on a command:

- midea-local stores status byte 3 as an untyped `additional` integer and does
  not send a half-load command.
- On model 76000228, one Home Assistant capture in that same issue (13 Oct 2026)
  reports `additional == 2` for half load and `0` for a full load. `wash_region`
  stayed `3`.
- This SDK already uses byte 3 for extra drying (`1` on, `0` off), from the
  T_0000_E1 plugin captures. A value of `2` would also decode as extra drying.

Confidence in a half-load command is low, so none is sent. The decoder records
this as a TODO until a capture shows a byte that is not the extra-drying flag.

## Custom transport

`Client` accepts any `Callable[[bytes], bytes]` as `send`. Useful for tests
with a mock transport, for a cloud integration, or for a pipeline of your own:

```python
from midea_dishwasher_api.protocol import assemble_frame

def fake_send(frame: bytes) -> bytes:
    return assemble_frame(b"...", 0x02)

client = Client(send=fake_send)
```

The low-level codec (`assemble_frame`, `parse_frame`, `build_query`,
`build_control`, `make_sum`) and the status decoder (`decode_response`) live in
`midea_dishwasher_api.protocol` and `midea_dishwasher_api.state` respectively —
outside the public `__init__.py`.

## Obtaining `token` and `key`

They are per-device credentials issued by the Midea cloud. Use one of the
existing tools (`midea-msmart`, `midea-beautiful-air`, `midea-discover`) to
extract them from your app account.

## Support

This SDK is built and maintained on personal time, on hardware bought for the purpose. If it is useful to you, consider [sponsoring the work](https://github.com/sponsors/roquerodrigo) — it keeps the devices, the testing and the releases coming.

## License

MIT — see `LICENSE`.
