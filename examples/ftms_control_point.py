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


class ControlResult(IntEnum):
    SUCCESS = 0x01
    NOT_SUPPORTED = 0x02
    INVALID_PARAMETER = 0x03
    OPERATION_FAILED = 0x04
    CONTROL_NOT_PERMITTED = 0x05


def encode_control_response(request_opcode: int, result: ControlResult) -> bytes:
    """FTMS Control Point response: Response Code (0x80), request opcode, result."""
    if not 0 <= request_opcode <= 255:
        raise ValueError("request opcode must fit in one byte")
    return bytes((0x80, request_opcode, result))


class ControlSession:
    """Application-layer control gate for one logical controller.

    The caller owns connection/session lifetime and must call release() on
    disconnect. No trainer commands are executed here.
    """

    def __init__(self) -> None:
        self.has_control = False

    def release(self) -> None:
        self.has_control = False

    def handle(self, value: bytes) -> tuple[ControlResult, ControlCommand | None]:
        """Validate request and return (FTMS result, accepted command)."""
        if not value:
            return ControlResult.INVALID_PARAMETER, None
        try:
            command = decode_control_point(value)
        except ValueError as exc:
            if str(exc).startswith("unsupported FTMS opcode"):
                return ControlResult.NOT_SUPPORTED, None
            return ControlResult.INVALID_PARAMETER, None

        if command.opcode == ControlOpcode.REQUEST_CONTROL:
            self.has_control = True
            return ControlResult.SUCCESS, command
        if not self.has_control:
            return ControlResult.CONTROL_NOT_PERMITTED, None
        if command.opcode == ControlOpcode.RESET:
            self.has_control = False
        return ControlResult.SUCCESS, command
