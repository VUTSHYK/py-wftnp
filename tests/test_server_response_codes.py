"""Protocol-defined WFTNP server response codes."""

import asyncio
from uuid import uuid4

import pytest

from wftnp.models import CharacteristicProperties
from wftnp.protocol import Frame, Opcode, decode_header, encode
from wftnp.server import CharacteristicSpec, ResponseCode, ServiceSpec, WftnpServer


@pytest.mark.asyncio
async def test_server_response_codes():
    service_id = uuid4()
    readable_id = uuid4()
    writable_id = uuid4()
    failing_id = uuid4()

    def fail_write(_value: bytes) -> None:
        raise ValueError("write rejected")

    server = WftnpServer(
        host="127.0.0.1",
        port=0,
        services=[ServiceSpec(service_id, (
            CharacteristicSpec(readable_id, CharacteristicProperties.READ, b"ok"),
            CharacteristicSpec(writable_id, CharacteristicProperties.WRITE),
            CharacteristicSpec(failing_id, CharacteristicProperties.WRITE, on_write=fail_write),
        ))],
    )
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)

    async def status(opcode: Opcode, payload: bytes) -> int:
        writer.write(encode(Frame(opcode, 42, payload=payload)))
        await writer.drain()
        header = decode_header(await reader.readexactly(6))
        await reader.readexactly(header.length)
        assert header.sequence == 42
        return header.status

    try:
        assert await status(Opcode.SERVICES, b"") == ResponseCode.SUCCESS
        assert await status(Opcode.SERVICES, b"extra") == ResponseCode.GENERIC_ERROR
        assert await status(Opcode.CHARACTERISTICS, uuid4().bytes) == ResponseCode.SERVICE_NOT_FOUND
        assert await status(Opcode.READ, uuid4().bytes) == ResponseCode.CHARACTERISTIC_NOT_FOUND
        assert await status(Opcode.READ, writable_id.bytes) == ResponseCode.OPERATION_NOT_SUPPORTED
        assert await status(Opcode.WRITE, readable_id.bytes + b"x") == ResponseCode.OPERATION_NOT_SUPPORTED
        assert await status(Opcode.WRITE, failing_id.bytes + b"x") == ResponseCode.CHARACTERISTIC_WRITE_FAILED
        assert (
            await status(Opcode.SUBSCRIBE, uuid4().bytes + b"\x01")
            == ResponseCode.CHARACTERISTIC_NOT_FOUND
        )
        assert (
            await status(Opcode.SUBSCRIBE, readable_id.bytes + b"\x01")
            == ResponseCode.OPERATION_NOT_SUPPORTED
        )
        assert await status(Opcode.WRITE, writable_id.bytes + b"ok") == ResponseCode.SUCCESS
        # A failed write must not kill the connection.
        assert await status(Opcode.READ, readable_id.bytes) == ResponseCode.SUCCESS
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.asyncio
async def test_invalid_message_type_response():
    server = WftnpServer(host="127.0.0.1", port=0)
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        writer.write(bytes((1, 99, 7, 0, 0, 0)))
        await writer.drain()
        response = await reader.readexactly(6)
        assert response == bytes((1, 99, 7, ResponseCode.INVALID_MESSAGE_TYPE, 0, 0))
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
