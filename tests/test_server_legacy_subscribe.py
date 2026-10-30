"""Regression tests for UUID-only and explicit-flag WFTNP subscriptions."""

import asyncio
from uuid import UUID, uuid4

import pytest

from wftnp.models import CharacteristicProperties
from wftnp.protocol import Frame, Opcode, decode_header, encode
from wftnp.server import CharacteristicSpec, ResponseCode, ServiceSpec, WftnpServer


async def receive(reader):
    header = decode_header(await asyncio.wait_for(reader.readexactly(6), 2))
    payload = await asyncio.wait_for(reader.readexactly(header.length), 2)
    return header, payload


@pytest.mark.asyncio
async def test_legacy_and_explicit_subscriptions_share_notification_path():
    service_uuid = uuid4()
    bike_uuid = UUID("00002ad2-0000-1000-8000-00805f9b34fb")
    server = WftnpServer(
        host="127.0.0.1",
        port=0,
        services=[
            ServiceSpec(service_uuid, (CharacteristicSpec(bike_uuid, CharacteristicProperties.NOTIFY),))
        ],
    )
    await server.start()
    legacy_reader, legacy_writer = await asyncio.open_connection("127.0.0.1", server.port)
    zwift_reader, zwift_writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        for writer, reader, sequence, payload in (
            (legacy_writer, legacy_reader, 0, bike_uuid.bytes),
            (zwift_writer, zwift_reader, 5, bike_uuid.bytes + b"\x01"),
        ):
            writer.write(encode(Frame(Opcode.SUBSCRIBE, sequence, payload=payload)))
            await writer.drain()
            response, data = await receive(reader)
            assert response.opcode == Opcode.SUBSCRIBE
            assert response.sequence == sequence
            assert response.status == ResponseCode.SUCCESS
            assert data == bike_uuid.bytes

        await server.notify_characteristic(bike_uuid, b"\x01\x02")
        for reader in (legacy_reader, zwift_reader):
            notification, data = await receive(reader)
            assert notification.opcode == Opcode.NOTIFY
            assert data == bike_uuid.bytes + b"\x01\x02"

        zwift_writer.write(encode(Frame(Opcode.SUBSCRIBE, 6, payload=bike_uuid.bytes + b"\x00")))
        await zwift_writer.drain()
        response, _ = await receive(zwift_reader)
        assert response.status == ResponseCode.SUCCESS
        await server.notify_characteristic(bike_uuid, b"\x03")
        notification, data = await receive(legacy_reader)
        assert notification.opcode == Opcode.NOTIFY
        assert data == bike_uuid.bytes + b"\x03"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(zwift_reader.readexactly(6), 0.1)
    finally:
        legacy_writer.close()
        zwift_writer.close()
        await legacy_writer.wait_closed()
        await zwift_writer.wait_closed()
        await server.stop()
