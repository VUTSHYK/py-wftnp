"""Immutable protocol and lifecycle values. No device-profile semantics."""

from dataclasses import dataclass
from enum import IntFlag, StrEnum
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    port: int = 36866

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")


@dataclass(frozen=True, slots=True)
class Advertisement:
    name: str
    endpoint: Endpoint
    addresses: tuple[str, ...] = ()
    serial_number: str | None = None
    mac_address: str | None = None
    services: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class Service:
    uuid: UUID


class CharacteristicProperties(IntFlag):
    READ = 1
    WRITE = 2
    NOTIFY = 4
    INDICATE = 8


@dataclass(frozen=True, slots=True)
class Characteristic:
    uuid: UUID
    properties: CharacteristicProperties


@dataclass(frozen=True, slots=True)
class Notification:
    characteristic: UUID
    value: bytes
    connection_id: int


class ClientState(StrEnum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    RESTORING = "restoring"
    READY = "ready"
    BACKOFF = "backoff"
