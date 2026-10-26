"""Tests for FTMS Control Point responses and command authorization."""

import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ftms_control_point.py"
SPEC = importlib.util.spec_from_file_location("ftms_session_example", EXAMPLE)
assert SPEC is not None and SPEC.loader is not None
ftms = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ftms
SPEC.loader.exec_module(ftms)


def test_erg_requires_request_control():
    session = ftms.ControlSession()
    erg = bytes.fromhex("05fa00")
    result, command = session.handle(erg)
    assert result == ftms.ControlResult.CONTROL_NOT_PERMITTED
    assert command is None
    assert ftms.encode_control_response(5, result) == bytes.fromhex("800505")

    result, command = session.handle(b"\x00")
    assert result == ftms.ControlResult.SUCCESS
    assert command is not None
    assert session.has_control

    result, command = session.handle(erg)
    assert result == ftms.ControlResult.SUCCESS
    assert command is not None
    assert command.target_power_watts == 250
    assert ftms.encode_control_response(5, result) == bytes.fromhex("800501")


def test_reset_and_disconnect_revoke_control():
    session = ftms.ControlSession()
    session.handle(b"\x00")
    result, command = session.handle(b"\x01")
    assert result == ftms.ControlResult.SUCCESS
    assert command is not None
    assert not session.has_control
    assert session.handle(bytes.fromhex("05fa00"))[0] == ftms.ControlResult.CONTROL_NOT_PERMITTED
    session.handle(b"\x00")
    session.release()
    assert not session.has_control


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"", 3),
        (b"\xff", 2),
        (b"\x05", 3),
        (b"\x00\x00", 3),
    ],
)
def test_bad_requests_have_ftms_result_codes(payload, expected):
    session = ftms.ControlSession()
    result, command = session.handle(payload)
    assert int(result) == expected
    assert command is None


def test_simulation_command_requires_control():
    session = ftms.ControlSession()
    simulation = bytes.fromhex("11dc0558022832")
    assert session.handle(simulation)[0] == ftms.ControlResult.CONTROL_NOT_PERMITTED
    session.handle(b"\x00")
    result, command = session.handle(simulation)
    assert result == ftms.ControlResult.SUCCESS
    assert command is not None
    assert command.grade_percent == 6.0


def test_response_opcode_range():
    with pytest.raises(ValueError):
        ftms.encode_control_response(256, ftms.ControlResult.SUCCESS)
