"""FTMS Control Point responses delivered as WFTNP TCP notifications.

WFTNP has no separate INDICATE opcode: the FTMS 0x80 response is carried
inside the Control Point characteristic NOTIFY frame.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from wftnp import (
    CharacteristicProperties,
    CharacteristicSpec,
    Endpoint,
    ServiceSpec,
    WftnpClient,
    WftnpServer,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ftms_control_point.py"
SPEC = importlib.util.spec_from_file_location("ftms_response_tcp_example", EXAMPLE)
assert SPEC is not None and SPEC.loader is not None
ftms = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ftms
SPEC.loader.exec_module(ftms)

FTMS_SERVICE = ftms.UUID("00001826-0000-1000-8000-00805f9b34fb")


@pytest.mark.asyncio
async def test_ftms_control_response_over_tcp():
    session = ftms.ControlSession()
    accepted = []
    server = None

    async def on_write(value: bytes) -> None:
        result, command = session.handle(value)
        if command is not None:
            accepted.append(command)
        await server.notify_characteristic(
            ftms.FITNESS_MACHINE_CONTROL_POINT,
            ftms.encode_control_response(value[0] if value else 0, result),
        )

    control = CharacteristicSpec(
        ftms.FITNESS_MACHINE_CONTROL_POINT,
        CharacteristicProperties.WRITE | CharacteristicProperties.NOTIFY,
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
        async with await client.subscribe(ftms.FITNESS_MACHINE_CONTROL_POINT) as subscription:
            for request, expected in [
                (bytes.fromhex("05fa00"), bytes.fromhex("800505")),
                (b"\x00", bytes.fromhex("800001")),
                (bytes.fromhex("05fa00"), bytes.fromhex("800501")),
                (b"\x01", bytes.fromhex("800101")),
                (bytes.fromhex("05fa00"), bytes.fromhex("800505")),
                (b"\xff", bytes.fromhex("80ff02")),
            ]:
                await client.write_characteristic(ftms.FITNESS_MACHINE_CONTROL_POINT, request)
                notification = await asyncio.wait_for(anext(subscription), 3)
                assert notification.characteristic == ftms.FITNESS_MACHINE_CONTROL_POINT
                assert notification.value == expected

        assert [command.opcode for command in accepted] == [
            ftms.ControlOpcode.REQUEST_CONTROL,
            ftms.ControlOpcode.SET_TARGET_POWER,
            ftms.ControlOpcode.RESET,
        ]
        assert accepted[1].target_power_watts == 250
    finally:
        await client.stop()
        await server.stop()
