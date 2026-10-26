"""Notification backpressure and write-timeout regression tests."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from wftnp.protocol import Opcode
from wftnp.server import ServerConnection, WftnpServer


@pytest.mark.asyncio
async def test_write_timeout_closes_stalled_peer():
    server = WftnpServer(host="127.0.0.1", port=0, write_timeout=0.05)
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        async with asyncio.timeout(2):
            while not server.connections:
                await asyncio.sleep(0.01)
        peer = server.connections[0]
        peer.writer.drain = AsyncMock(side_effect=lambda: asyncio.sleep(10))
        with pytest.raises(TimeoutError):
            await peer._send_frame(Opcode.NOTIFY, 0, payload=b"test")
    finally:
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(server.stop(), 2)


@pytest.mark.asyncio
async def test_notification_limit_blocks_producer_until_slot_frees():
    characteristic = uuid4()
    server = WftnpServer(host="127.0.0.1", port=0, max_pending_notifications=1)
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    release = asyncio.Event()
    entered = asyncio.Event()
    try:
        async with asyncio.timeout(2):
            while not server.connections:
                await asyncio.sleep(0.01)
        peer = server.connections[0]
        peer.subscriptions.add(characteristic)
        server._register_subscription(peer, characteristic)

        async def slow_send(*args, **kwargs):
            entered.set()
            await release.wait()

        peer._send_frame = slow_send
        first = asyncio.create_task(server.notify_characteristic(characteristic, b"one"))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(server.notify_characteristic(characteristic, b"two"))
        await asyncio.sleep(0.05)
        assert not second.done()
        assert len(server._notify_tasks) == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert not server._notify_tasks
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(server.stop(), 2)


@pytest.mark.asyncio
async def test_shutdown_cancels_blocked_notification():
    characteristic = uuid4()
    server = WftnpServer(host="127.0.0.1", port=0, max_pending_notifications=1)
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    entered = asyncio.Event()
    try:
        async with asyncio.timeout(2):
            while not server.connections:
                await asyncio.sleep(0.01)
        peer = server.connections[0]
        peer.subscriptions.add(characteristic)
        server._register_subscription(peer, characteristic)

        async def blocked_send(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        peer._send_frame = blocked_send
        first = asyncio.create_task(server.notify_characteristic(characteristic, b"one"))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(server.notify_characteristic(characteristic, b"two"))
        await asyncio.sleep(0)
        await asyncio.wait_for(server.stop(), 2)
        await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 2)
        assert not server._notify_tasks
    finally:
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(server.stop(), 2)
