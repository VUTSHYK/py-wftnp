"""Bounded multi-client stress and shutdown checks for the WFTNP TCP server."""

import asyncio
import contextlib
import struct
from uuid import UUID

import pytest

from wftnp.models import CharacteristicProperties
from wftnp.server import CharacteristicSpec, ServiceSpec, WftnpServer

SERVICE = UUID("00001826-0000-1000-8000-00805f9b34fb")
CHAR = UUID("00002ad2-0000-1000-8000-00805f9b34fb")
HEADER = struct.Struct("!BBBBH")


async def read_frame(reader):
    version, opcode, seq, status, size = HEADER.unpack(await asyncio.wait_for(reader.readexactly(6), 5))
    payload = await asyncio.wait_for(reader.readexactly(size), 5)
    return version, opcode, seq, status, payload


async def connect_subscriber(server):
    reader, writer = await asyncio.open_connection(server.host, server.port)
    payload = CHAR.bytes + b"\x01"
    writer.write(HEADER.pack(1, 5, 17, 0, len(payload)) + payload)
    await writer.drain()
    assert await read_frame(reader) == (1, 5, 17, 0, CHAR.bytes)
    return reader, writer


@pytest.fixture
async def server():
    instance = WftnpServer(
        host="127.0.0.1",
        port=0,
        services=[ServiceSpec(SERVICE, (CharacteristicSpec(CHAR, CharacteristicProperties.NOTIFY),))],
        write_timeout=0.5,
        max_pending_notifications=16,
    )
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.mark.parametrize("count", [10, 50])
async def test_concurrent_subscribers_receive_notifications(server, count):
    peers = await asyncio.gather(*(connect_subscriber(server) for _ in range(count)))
    try:
        for index in range(12):
            payload = struct.pack("!I", index)
            await asyncio.wait_for(server.notify_characteristic(CHAR, payload), 10)
            frames = await asyncio.gather(*(read_frame(reader) for reader, _ in peers))
            assert all(frame == (1, 6, 0, 0, CHAR.bytes + payload) for frame in frames)
        await asyncio.sleep(0)
        assert not server._notify_tasks
        assert not server._notify_calls
        assert len(server.connections) == count
    finally:
        for _, writer in peers:
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for _, writer in peers))


async def test_disconnect_half_subscribers_during_delivery(server):
    peers = await asyncio.gather(*(connect_subscriber(server) for _ in range(20)))
    try:
        for _, writer in peers[:10]:
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for _, writer in peers[:10]))
        for index in range(10):
            payload = bytes((index,))
            await asyncio.wait_for(server.notify_characteristic(CHAR, payload), 10)
            frames = await asyncio.gather(*(read_frame(reader) for reader, _ in peers[10:]))
            assert all(frame == (1, 6, 0, 0, CHAR.bytes + payload) for frame in frames)
        await asyncio.wait_for(server.stop(), 5)
        assert not server.connections
        assert not server._subscriptions
        assert not server._notify_tasks
        assert not server._notify_calls
    finally:
        for _, writer in peers:
            writer.close()
        await asyncio.gather(
            *(writer.wait_closed() for _, writer in peers),
            return_exceptions=True,
        )


async def test_stop_during_notification_burst_and_restart(server):
    peers = await asyncio.gather(*(connect_subscriber(server) for _ in range(20)))
    producers = [
        asyncio.create_task(server.notify_characteristic(CHAR, struct.pack("!I", index)))
        for index in range(30)
    ]
    try:
        await asyncio.sleep(0)
        await asyncio.wait_for(server.stop(), 5)
        await asyncio.wait_for(asyncio.gather(*producers, return_exceptions=True), 5)
        assert not server._notify_calls
        assert not server._notify_tasks
        assert not server._connections
        assert not server._subscriptions
        await server.start()
        reader, writer = await connect_subscriber(server)
        try:
            await asyncio.wait_for(server.notify_characteristic(CHAR, b"restart"), 5)
            assert await read_frame(reader) == (1, 6, 0, 0, CHAR.bytes + b"restart")
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        for task in producers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*producers, return_exceptions=True)
        for _, writer in peers:
            writer.close()
        with contextlib.suppress(Exception):
            await asyncio.gather(
                *(writer.wait_closed() for _, writer in peers),
                return_exceptions=True,
            )
