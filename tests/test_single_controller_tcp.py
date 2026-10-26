"""Connection-scoped FTMS control with multiple telemetry subscribers."""

import asyncio
from uuid import UUID

import pytest

from wftnp import (
    CharacteristicProperties,
    CharacteristicSpec,
    Endpoint,
    ServiceSpec,
    WftnpClient,
    WftnpServer,
)

SERVICE = UUID("00001826-0000-1000-8000-00805f9b34fb")
CONTROL = UUID("00002ad9-0000-1000-8000-00805f9b34fb")
BIKE = UUID("00002ad2-0000-1000-8000-00805f9b34fb")


@pytest.mark.asyncio
async def test_single_controller_observers_and_handover():
    owner = None
    accepted = []
    disconnected = []
    owner_released = asyncio.Event()
    server = None

    async def on_write(connection, value):
        nonlocal owner
        opcode = value[0]
        if opcode == 0 and (owner is None or owner is connection):
            owner = connection
            result = 1
        elif connection is owner:
            accepted.append((connection, value))
            result = 1
        else:
            result = 5
        await server.notify_connection(connection, CONTROL, bytes((0x80, opcode, result)))

    def on_disconnect(connection):
        nonlocal owner
        disconnected.append(connection)
        if owner is connection:
            owner = None
            owner_released.set()

    server = WftnpServer(
        port=0,
        services=[ServiceSpec(SERVICE, (
            CharacteristicSpec(CONTROL, CharacteristicProperties.WRITE | CharacteristicProperties.NOTIFY,
                               on_write_with_connection=on_write),
            CharacteristicSpec(BIKE, CharacteristicProperties.NOTIFY),
        ))],
        on_disconnect_with_connection=on_disconnect,
        idle_timeout=60,
    )
    await server.start()
    a = WftnpClient(Endpoint("127.0.0.1", server.port), heartbeat_interval=None)
    b = WftnpClient(Endpoint("127.0.0.1", server.port), heartbeat_interval=None)
    try:
        await a.start()
        await b.start()
        await a.wait_ready(timeout=3)
        await b.wait_ready(timeout=3)
        async with await a.subscribe(CONTROL) as a_control, await b.subscribe(CONTROL) as b_control, \
                   await a.subscribe(BIKE) as a_bike, await b.subscribe(BIKE) as b_bike:
            await a.write_characteristic(CONTROL, b"\x00")
            assert (await asyncio.wait_for(anext(a_control), 3)).value == b"\x80\x00\x01"
            await b.write_characteristic(CONTROL, b"\x00")
            assert (await asyncio.wait_for(anext(b_control), 3)).value == b"\x80\x00\x05"
            await b.write_characteristic(CONTROL, b"\x05\xfa\x00")
            assert (await asyncio.wait_for(anext(b_control), 3)).value == b"\x80\x05\x05"
            assert accepted == []
            await server.notify_characteristic(BIKE, b"\x01\x02")
            assert (await asyncio.wait_for(anext(a_bike), 3)).value == b"\x01\x02"
            assert (await asyncio.wait_for(anext(b_bike), 3)).value == b"\x01\x02"
            await a.write_characteristic(CONTROL, b"\x05\xfa\x00")
            assert (await asyncio.wait_for(anext(a_control), 3)).value == b"\x80\x05\x01"
            assert len(accepted) == 1
            await a.stop()
            await asyncio.wait_for(owner_released.wait(), 3)
            await b.write_characteristic(CONTROL, b"\x00")
            assert (await asyncio.wait_for(anext(b_control), 3)).value == b"\x80\x00\x01"
            assert owner is not None
        assert disconnected
    finally:
        await a.stop()
        await b.stop()
        await server.stop()
