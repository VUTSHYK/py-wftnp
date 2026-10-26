"""Single-controller admission and idle timeout integration tests."""

import asyncio
from contextlib import suppress

import pytest

from wftnp import WftnpServer


@pytest.mark.asyncio
async def test_second_client_rejected_and_disconnect_releases():
    disconnected = []
    server = WftnpServer(
        port=0, max_connections=1, idle_timeout=60,
        on_disconnect=lambda: disconnected.append(True),
    )
    await server.start()
    first_reader, first_writer = await asyncio.open_connection("127.0.0.1", server.port)
    second_reader, second_writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        assert await asyncio.wait_for(second_reader.read(1), 2) == b""
        assert len(server.connections) == 1
        first_writer.close()
        await first_writer.wait_closed()
        async with asyncio.timeout(2):
            while not disconnected:
                await asyncio.sleep(0.01)
        assert len(server.connections) == 0
        third_reader, third_writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            assert len(server.connections) == 1
        finally:
            third_writer.close()
            await third_writer.wait_closed()
    finally:
        second_writer.close()
        await second_writer.wait_closed()
        first_writer.close()
        await server.stop()


@pytest.mark.asyncio
async def test_idle_connection_expires():
    disconnected = []
    server = WftnpServer(
        port=0, max_connections=1, idle_timeout=0.05,
        on_disconnect=lambda: disconnected.append(True),
    )
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        assert await asyncio.wait_for(reader.read(1), 2) == b""
        assert disconnected == [True]
        assert len(server.connections) == 0
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
