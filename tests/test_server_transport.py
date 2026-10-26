"""Wire-level regression tests for WFTNP server notification lifecycle."""

import asyncio
import struct
from uuid import UUID

import pytest

from wftnp.models import CharacteristicProperties
from wftnp.server import CharacteristicSpec, ServiceSpec, WftnpServer

SERVICE = UUID("00001826-0000-1000-8000-00805f9b34fb")
CHAR = UUID("00002ad2-0000-1000-8000-00805f9b34fb")
HEADER = struct.Struct("!BBBBH")


async def frame(reader):
    header = await asyncio.wait_for(reader.readexactly(6), 1)
    version, opcode, seq, status, length = HEADER.unpack(header)
    return version, opcode, seq, status, await asyncio.wait_for(reader.readexactly(length), 1)


async def request(reader, writer, opcode, sequence, payload):
    writer.write(HEADER.pack(1, opcode, sequence, 0, len(payload)) + payload)
    await writer.drain()
    return await frame(reader)


@pytest.fixture
async def transport():
    server = WftnpServer(
        port=0,
        services=[ServiceSpec(SERVICE, (CharacteristicSpec(CHAR, CharacteristicProperties.NOTIFY),))],
        write_timeout=0.1,
    )
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


async def subscribe(server):
    reader, writer = await asyncio.open_connection(server.host, server.port)
    assert await request(reader, writer, 5, 7, CHAR.bytes + b"\x01") == (
        1, 5, 7, 0, CHAR.bytes
    )
    return reader, writer


async def test_characteristics_exact_wire_layout(transport):
    reader, writer = await asyncio.open_connection(transport.host, transport.port)
    try:
        assert await request(reader, writer, 2, 9, SERVICE.bytes) == (
            1, 2, 9, 0, SERVICE.bytes + CHAR.bytes + b"\x04"
        )
    finally:
        writer.close()
        await writer.wait_closed()


async def test_multiple_subscribers_and_notify_wire(transport):
    a, wa = await subscribe(transport)
    b, wb = await subscribe(transport)
    try:
        await transport.notify_characteristic(CHAR, b"data")
        expected = (1, 6, 0, 0, CHAR.bytes + b"data")
        assert await frame(a) == expected
        assert await frame(b) == expected
        await asyncio.sleep(0)  # Let completion callbacks retire tracked tasks.
        assert not transport._notify_tasks
    finally:
        wa.close()
        wb.close()
        await asyncio.gather(wa.wait_closed(), wb.wait_closed())


async def test_unsubscribe_and_disconnect_cleanup(transport):
    a, writer = await subscribe(transport)
    assert await request(a, writer, 5, 8, CHAR.bytes + b"\x00") == (
        1, 5, 8, 0, CHAR.bytes
    )
    assert CHAR not in transport._subscriptions
    writer.close()
    await writer.wait_closed()
    await asyncio.wait_for(transport.stop(), 1)
    assert not transport._connections


async def test_failed_notification_detaches_only_failed_peer(transport, monkeypatch):
    a, wa = await subscribe(transport)
    b, wb = await subscribe(transport)
    peers = tuple(transport._connections)
    bad = next(
        c
        for c in peers
        if c.writer is not None
        and c.writer.get_extra_info("peername")[1] == wa.get_extra_info("sockname")[1]
    )
    original = bad._send_frame

    async def fail(opcode, sequence, *, payload=b"", status=0):
        if int(opcode) == 6:
            raise ConnectionError("injected write failure")
        return await original(opcode, sequence, payload=payload, status=status)

    monkeypatch.setattr(bad, "_send_frame", fail)
    try:
        await transport.notify_characteristic(CHAR, b"ok")
        assert await frame(b) == (1, 6, 0, 0, CHAR.bytes + b"ok")
        assert bad not in transport._connections
        assert bad not in transport._subscriptions.get(CHAR, ())
    finally:
        wa.close()
        wb.close()
        await asyncio.gather(wa.wait_closed(), wb.wait_closed())


async def test_slow_peer_timeout_does_not_block_other_peer(transport, monkeypatch):
    a, wa = await subscribe(transport)
    b, wb = await subscribe(transport)
    bad = next(
        c for c in transport._connections
        if c.writer.get_extra_info("peername")[1] == wa.get_extra_info("sockname")[1]
    )
    original = bad._send_frame

    async def stall(opcode, sequence, *, payload=b"", status=0):
        if int(opcode) == 6:
            await asyncio.sleep(10)
        return await original(opcode, sequence, payload=payload, status=status)

    monkeypatch.setattr(bad, "_send_frame", stall)
    try:
        task = asyncio.create_task(transport.notify_characteristic(CHAR, b"ok"))
        assert await frame(b) == (1, 6, 0, 0, CHAR.bytes + b"ok")
        assert not task.done()
        await asyncio.wait_for(transport.stop(), 1)
        await asyncio.wait_for(task, 1)
        assert not transport._notify_tasks
        assert not transport._notify_calls
    finally:
        wa.close()
        wb.close()
        await asyncio.gather(wa.wait_closed(), wb.wait_closed())


async def test_stop_with_saturated_notification_slots(transport, monkeypatch):
    transport._notify_slots = asyncio.Semaphore(1)
    reader, writer = await subscribe(transport)
    peer = transport.connections[0]
    entered = asyncio.Event()

    async def stall(opcode, sequence, *, payload=b"", status=0):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(peer, "_send_frame", stall)
    first = asyncio.create_task(transport.notify_characteristic(CHAR, b"one"))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(transport.notify_characteristic(CHAR, b"two"))
    try:
        await asyncio.sleep(0)
        await asyncio.wait_for(transport.stop(), 1)
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert not transport._notify_calls
        assert not transport._notify_tasks
    finally:
        writer.close()
        await writer.wait_closed()


async def test_stop_and_restart(transport):
    reader, writer = await subscribe(transport)
    await transport.stop()
    assert not transport._notify_tasks
    assert not transport._connections
    assert not transport._subscriptions
    writer.close()
    await writer.wait_closed()
    await transport.start()
    reader2, writer2 = await asyncio.open_connection(transport.host, transport.port)
    try:
        assert await request(reader2, writer2, 1, 3, b"") == (1, 1, 3, 0, SERVICE.bytes)
    finally:
        writer2.close()
        await writer2.wait_closed()
