"""Regression tests for FTMS Control Point example decoding."""

import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ftms_control_point.py"
SPEC = importlib.util.spec_from_file_location("ftms_control_example", EXAMPLE)
assert SPEC is not None and SPEC.loader is not None
ftms = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ftms
SPEC.loader.exec_module(ftms)


def test_request_control_and_reset():
    assert ftms.decode_control_point(b"\x00").opcode == ftms.ControlOpcode.REQUEST_CONTROL
    assert ftms.decode_control_point(b"\x01").opcode == ftms.ControlOpcode.RESET


def test_target_power_erg():
    command = ftms.decode_control_point(bytes.fromhex("05fa00"))
    assert command.opcode == ftms.ControlOpcode.SET_TARGET_POWER
    assert command.target_power_watts == 250


def test_indoor_bike_simulation():
    # Wind 1.5 m/s, grade 6%, Crr 0.004, Cw 0.5.
    command = ftms.decode_control_point(bytes.fromhex("11dc0558022832"))
    assert command.opcode == ftms.ControlOpcode.SET_INDOOR_BIKE_SIMULATION
    assert command.wind_speed_mps == 1.5
    assert command.grade_percent == 6.0
    assert command.rolling_resistance == 0.004
    assert command.wind_resistance == 0.5


@pytest.mark.parametrize("value", [b"", b"\xff", b"\x05", b"\x05\xfa\x00\x00", b"\x11\x00"])
def test_invalid_control_requests(value):
    with pytest.raises(ValueError):
        ftms.decode_control_point(value)
