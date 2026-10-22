import asyncio
import struct
from uuid import UUID

import pytest

from wftnp import CharacteristicProperties, Endpoint, ProtocolError
from wftnp.protocol import (
    Frame,
    Opcode,
    decode_characteristics,
    decode_header,
    decode_services,
    decode_value,
    encode,
    validate_response,
)
from wftnp.server import CharacteristicSpec, ServiceSpec, WftnpServer

from .support.server import CHARACTERISTIC, SERVICE


def test_known_wire_frame():
    frame = Frame(Opcode.SUBSCRIBE, 42, payload=CHARACTERISTIC.bytes + b"\x01")
    assert encode(frame) == bytes.fromhex("01052a00001100002ad200001000800000805f9b34fb01")
    header = decode_header(encode(frame)[:6])
    assert (header.opcode, header.sequence, header.length) == (Opcode.SUBSCRIBE, 42, 17)


@pytest.mark.parametrize(
    "data", [b"", b"12345", b"1234567", bytes.fromhex("020100000000"), bytes.fromhex("01ff00000000")]
)
def test_invalid_headers(data):
    with pytest.raises(ProtocolError):
        decode_header(data)


@pytest.mark.parametrize("sequence,status", [(-1, 0), (256, 0), (0, -1), (0, 256)])
def test_invalid_encode_fields(sequence, status):
    with pytest.raises(ValueError):
        encode(Frame(Opcode.READ, sequence, status))


def test_payload_length_limits():
    assert len(encode(Frame(Opcode.WRITE, 0, payload=b"x" * 65535))) == 65541
    with pytest.raises(ValueError):
        encode(Frame(Opcode.WRITE, 0, payload=b"x" * 65536))


def test_uuid_byte_order_and_unknown_property_bits():
    assert decode_services(SERVICE.bytes)[0].uuid == SERVICE
    assert decode_services(b"") == ()
    result = decode_characteristics(SERVICE.bytes + CHARACTERISTIC.bytes + b"\x87", SERVICE)[0]
    assert result.uuid == CHARACTERISTIC
    assert result.properties & CharacteristicProperties.NOTIFY
    assert int(result.properties) == 0x87


async def test_server_characteristics_response_includes_service_uuid_prefix():
    server = WftnpServer(
        host="127.0.0.1",
        port=0,
        services=(
            ServiceSpec(
                uuid=SERVICE,
                characteristics=(CharacteristicSpec(uuid=CHARACTERISTIC, properties=0x07, value=b"value"),),
            ),
        ),
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            request = encode(Frame(Opcode.CHARACTERISTICS, 7, payload=SERVICE.bytes))
            writer.write(request)
            await writer.drain()

            header = decode_header(await asyncio.wait_for(reader.readexactly(6), timeout=1.0))
            assert header.opcode == Opcode.CHARACTERISTICS
            assert header.sequence == 7
            assert header.status == 0
            payload = await asyncio.wait_for(reader.readexactly(header.length), timeout=1.0)
            assert payload[:16] == SERVICE.bytes

            characteristics = decode_characteristics(payload, SERVICE)
            assert [item.uuid for item in characteristics] == [CHARACTERISTIC]
            assert characteristics[0].properties == (
                CharacteristicProperties.READ
                |
                CharacteristicProperties.WRITE
                |
                CharacteristicProperties.NOTIFY
            )
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.parametrize("payload", [b"x", b"x" * 17])
def test_bad_service_list(payload):
    with pytest.raises(ProtocolError):
        decode_services(payload)


def test_bad_characteristic_list_and_uuid():
    with pytest.raises(ProtocolError):
        decode_characteristics(SERVICE.bytes + b"x", SERVICE)
    with pytest.raises(ProtocolError):
        decode_value(b"", SERVICE)
    with pytest.raises(ProtocolError):
        decode_value(CHARACTERISTIC.bytes, SERVICE)


def test_response_validation():
    request = Frame(Opcode.WRITE, 8, payload=CHARACTERISTIC.bytes + b"x")
    validate_response(Frame(Opcode.WRITE, 8, payload=CHARACTERISTIC.bytes), request)
    validate_response(Frame(Opcode.WRITE, 8, 77), request)
    for frame in [
        Frame(Opcode.WRITE, 9),
        Frame(Opcode.READ, 8),
        Frame(Opcode.WRITE, 8, payload=CHARACTERISTIC.bytes + b"x"),
    ]:
        with pytest.raises(ProtocolError):
            validate_response(frame, request)


@pytest.mark.parametrize("host,port", [("", 1), (" ", 1), ("host", 0), ("host", 65536)])
def test_endpoint_validation(host, port):
    with pytest.raises(ValueError):
        Endpoint(host, port)


def test_opaque_value():
    value = struct.pack("<H", 0x1234)
    assert decode_value(CHARACTERISTIC.bytes + value, CHARACTERISTIC) == value
    assert UUID(bytes=CHARACTERISTIC.bytes) == CHARACTERISTIC
