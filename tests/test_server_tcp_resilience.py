"""End-to-end TCP lifecycle and subscription isolation for the WFTNP server."""

import asyncio
from uuid import uuid4

import pytest

from wftnp.models import CharacteristicProperties
from wftnp.protocol import Frame, Opcode, decode_header, encode
from wftnp.server import CharacteristicSpec, ServiceSpec, WftnpServer


async def exchange(reader, writer, opcode, sequence, payload=b""):
    writer.write(encode(Frame(opcode, sequence, payload=payload)))
    await writer.drain()
    header = decode_header(await asyncio.wait_for(reader.readexactly(6), 2))
    body = await asyncio.wait_for(reader.readexactly(header.length), 2)
    assert header.sequence == sequence
    assert header.opcode == opcode
    assert header.status == 0
    return body


@pytest.mark.asyncio
async def test_two_clients_have_independent_subscriptions():
    characteristic = uuid4()
    service = ServiceSpec(
        uuid4(),
        (CharacteristicSpec(characteristic, CharacteristicProperties.NOTIFY),),
    )
    server = WftnpServer(host="127.0.0.1", port=0, services=[service])
    await server.start()
    first_reader, first_writer = await asyncio.open_connection("127.0.0.1", server.port)
    second_reader, second_writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        subscribed = await exchange(
            first_reader, first_writer, Opcode.SUBSCRIBE, 1, characteristic.bytes + b"\x01"
        )
        assert subscribed == characteristic.bytes
        await server.notify_characteristic(characteristic, b"one")
        header = decode_header(await asyncio.wait_for(first_reader.readexactly(6), 2))
        assert header.opcode == Opcode.NOTIFY
        assert await first_reader.readexactly(header.length) == characteristic.bytes + b"one"

        # The second client has no subscription and must not receive this notification.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(second_reader.readexactly(6), 0.1)
        assert await exchange(second_reader, second_writer, Opcode.SERVICES, 2) == service.uuid.bytes

        unsubscribed = await exchange(
            first_reader, first_writer, Opcode.SUBSCRIBE, 3, characteristic.bytes + b"\x00"
        )
        assert unsubscribed == characteristic.bytes
        await server.notify_characteristic(characteristic, b"two")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(first_reader.readexactly(6), 0.1)
    finally:
        first_writer.close()
        second_writer.close()
        await asyncio.gather(first_writer.wait_closed(), second_writer.wait_closed())
        await asyncio.wait_for(server.stop(), 3)


@pytest.mark.asyncio
async def test_partial_frame_disconnect_and_restart():
    server = WftnpServer(host="127.0.0.1", port=0)
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        writer.write(b"\x01\x01\x05")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(reader.read(), 2)
        await asyncio.sleep(0)
        await asyncio.wait_for(server.stop(), 3)
        await server.start()
        new_reader, new_writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            assert await exchange(new_reader, new_writer, Opcode.SERVICES, 4) == b""
        finally:
            new_writer.close()
            await new_writer.wait_closed()
    finally:
        await asyncio.wait_for(server.stop(), 3)


@pytest.mark.asyncio
async def test_stop_closes_idle_connected_clients():
    server = WftnpServer(host="127.0.0.1", port=0)
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        await asyncio.wait_for(server.stop(), 3)
        assert await asyncio.wait_for(reader.read(), 2) == b""
    finally:
        writer.close()
        await writer.wait_closed()
