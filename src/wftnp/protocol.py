"""WFTNP v1 wire codec. UUID payload values are opaque to this module."""

import struct
from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

from .exceptions import ProtocolError
from .models import Characteristic, CharacteristicProperties, Service

HEADER = struct.Struct("!BBBBH")
MAX_PAYLOAD = 65535


class Opcode(IntEnum):
    SERVICES = 1
    CHARACTERISTICS = 2
    READ = 3
    WRITE = 4
    SUBSCRIBE = 5
    NOTIFY = 6


@dataclass(frozen=True, slots=True)
class Header:
    opcode: Opcode
    sequence: int
    status: int
    length: int


@dataclass(frozen=True, slots=True)
class Frame:
    opcode: Opcode | int
    sequence: int
    status: int = 0
    payload: bytes = b""


def encode(frame: Frame) -> bytes:
    if not 0 <= frame.sequence <= 255 or not 0 <= frame.status <= 255:
        raise ValueError("sequence and status must fit in one byte")
    if len(frame.payload) > MAX_PAYLOAD:
        raise ValueError("payload exceeds the WFTNP 16-bit length limit")
    return HEADER.pack(1, frame.opcode, frame.sequence, frame.status, len(frame.payload)) + frame.payload


def decode_header(data: bytes) -> Header:
    if len(data) != HEADER.size:
        raise ProtocolError("WFTNP header must be exactly six bytes")
    version, opcode, sequence, status, length = HEADER.unpack(data)
    if version != 1:
        raise ProtocolError(f"Unsupported WFTNP version {version}")
    try:
        return Header(Opcode(opcode), sequence, status, length)
    except ValueError as exc:
        raise ProtocolError(f"Unknown WFTNP opcode {opcode}") from exc


def decode_services(payload: bytes) -> tuple[Service, ...]:
    if len(payload) % 16:
        raise ProtocolError("Service list is not a multiple of 16 bytes")
    return tuple(Service(UUID(bytes=payload[i : i + 16])) for i in range(0, len(payload), 16))


def decode_characteristics(payload: bytes, service: UUID) -> tuple[Characteristic, ...]:
    records = decode_value(payload, service)
    if len(records) % 17:
        raise ProtocolError("Characteristic list is not a multiple of 17 bytes")
    return tuple(
        Characteristic(UUID(bytes=records[i : i + 16]), CharacteristicProperties(records[i + 16]))
        for i in range(0, len(records), 17)
    )


def decode_value(payload: bytes, characteristic: UUID) -> bytes:
    if len(payload) < 16 or payload[:16] != characteristic.bytes:
        raise ProtocolError("Response UUID does not match the request")
    return payload[16:]


def validate_response(frame: Frame, request: Frame) -> None:
    if frame.opcode != request.opcode or frame.sequence != request.sequence:
        raise ProtocolError("Response does not match the outstanding request")
    if frame.status:
        return  # Error payloads are not required to contain a UUID.
    if request.opcode == Opcode.SERVICES:
        decode_services(frame.payload)
    elif request.opcode == Opcode.CHARACTERISTICS:
        decode_characteristics(frame.payload, UUID(bytes=request.payload[:16]))
    else:
        value = decode_value(frame.payload, UUID(bytes=request.payload[:16]))
        if request.opcode in (Opcode.WRITE, Opcode.SUBSCRIBE) and value:
            raise ProtocolError("Write/subscribe response contains unexpected trailing bytes")
