"""Transport-independent FTMS Control Point command decoding example.

This decoder parses requests only. An application must enforce control ownership,
supported features, and trainer safety before applying commands.
"""

from dataclasses import dataclass
from enum import IntEnum
from struct import unpack_from
from uuid import UUID

FITNESS_MACHINE_CONTROL_POINT = UUID("00002ad9-0000-1000-8000-00805f9b34fb")


class ControlOpcode(IntEnum):
    REQUEST_CONTROL = 0x00
    RESET = 0x01
    SET_TARGET_POWER = 0x05
    SET_INDOOR_BIKE_SIMULATION = 0x11


@dataclass(frozen=True, slots=True)
class ControlCommand:
    opcode: ControlOpcode
    target_power_watts: int | None = None
    wind_speed_mps: float | None = None
    grade_percent: float | None = None
    rolling_resistance: float | None = None
    wind_resistance: float | None = None


def decode_control_point(value: bytes) -> ControlCommand:
    """Parse supported FTMS control requests; reject malformed/unknown requests."""
    if not value:
        raise ValueError("empty FTMS Control Point request")
    try:
        opcode = ControlOpcode(value[0])
    except ValueError as exc:
        raise ValueError(f"unsupported FTMS opcode: 0x{value[0]:02x}") from exc

    expected = {
        ControlOpcode.REQUEST_CONTROL: 1,
        ControlOpcode.RESET: 1,
        ControlOpcode.SET_TARGET_POWER: 3,
        ControlOpcode.SET_INDOOR_BIKE_SIMULATION: 7,
    }[opcode]
    if len(value) != expected:
        raise ValueError(f"opcode 0x{opcode:02x} requires {expected} bytes")

    if opcode == ControlOpcode.SET_TARGET_POWER:
        return ControlCommand(opcode, target_power_watts=unpack_from("<h", value, 1)[0])
    if opcode == ControlOpcode.SET_INDOOR_BIKE_SIMULATION:
        wind, grade, crr, cw = unpack_from("<hhBB", value, 1)
        return ControlCommand(
            opcode,
            wind_speed_mps=wind / 1000,
            grade_percent=grade / 100,
            rolling_resistance=crr / 10000,
            wind_resistance=cw / 100,
        )
    return ControlCommand(opcode)
