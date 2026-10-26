"""End-to-end FTMS Control Point WRITE over real WFTNP TCP sockets.

No trainer hardware is driven: the application callback records decoded commands.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from wftnp import (
    CharacteristicProperties,
    CharacteristicSpec,
    Endpoint,
    OperationRejected,
    ServiceSpec,
    WftnpClient,
    WftnpServer,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ftms_control_point.py"
SPEC = importlib.util.spec_from_file_location("ftms_control_tcp_example", EXAMPLE)
assert SPEC is not None and SPEC.loader is not None
ftms = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ftms
SPEC.loader.exec_module(ftms)

FTMS_SERVICE = ftms.UUID("00001826-0000-1000-8000-00805f9b34fb")


@pytest.mark.asyncio
async def test_control_point_erg_and_grade_over_tcp():
    commands = []

    async def on_write(value: bytes) -> None:
        commands.append(ftms.decode_control_point(value))

    control = CharacteristicSpec(
        ftms.FITNESS_MACHINE_CONTROL_POINT,
        CharacteristicProperties.WRITE,
        on_write=on_write,
    )
    server = WftnpServer(
        host="127.0.0.1",
        port=0,
        services=[ServiceSpec(FTMS_SERVICE, (control,))],
    )
    await server.start()
    client = WftnpClient(Endpoint("127.0.0.1", server.port), heartbeat_interval=None)
    try:
        await client.start()
        await client.wait_ready(timeout=3)
        await client.write_characteristic(ftms.FITNESS_MACHINE_CONTROL_POINT, b"\x00")
        await client.write_characteristic(ftms.FITNESS_MACHINE_CONTROL_POINT, bytes.fromhex("05fa00"))
        await client.write_characteristic(ftms.FITNESS_MACHINE_CONTROL_POINT, bytes.fromhex("11dc0558022832"))

        assert [command.opcode for command in commands] == [
            ftms.ControlOpcode.REQUEST_CONTROL,
            ftms.ControlOpcode.SET_TARGET_POWER,
            ftms.ControlOpcode.SET_INDOOR_BIKE_SIMULATION,
        ]
        assert commands[1].target_power_watts == 250
        assert commands[2].grade_percent == 6.0
        assert commands[2].wind_speed_mps == 1.5

        with pytest.raises(OperationRejected):
            await client.write_characteristic(ftms.FITNESS_MACHINE_CONTROL_POINT, b"\x05")
        assert len(commands) == 3
    finally:
        await client.stop()
        await server.stop()
