"""WFTNP server transport support. The generic protocol remains byte-oriented."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from uuid import UUID

from .exceptions import ProtocolError, WftnpError
from .models import CharacteristicProperties, Endpoint
from .protocol import Frame, Opcode, decode_header, encode


@dataclass(slots=True)
class CharacteristicSpec:
    """Uninterpreted GATT-like characteristic value and access policy."""

    uuid: UUID
    properties: CharacteristicProperties | int = CharacteristicProperties.READ
    value: bytes = b""
    on_write: Callable[[bytes], bytes | Awaitable[bytes] | None] | None = None

    def __post_init__(self) -> None:
        self.properties = CharacteristicProperties(self.properties)


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    uuid: UUID
    characteristics: tuple[CharacteristicSpec, ...] = ()


class _ServerRegistry:
    def __init__(self, services: Iterable[ServiceSpec]) -> None:
        self.services = tuple(services)
        self._by_service: dict[UUID, ServiceSpec] = {}
        self._by_characteristic: dict[UUID, CharacteristicSpec] = {}
        for service in self.services:
            self._by_service[service.uuid] = service
            for characteristic in service.characteristics:
                self._by_characteristic[characteristic.uuid] = characteristic

    def service_uuids(self) -> tuple[UUID, ...]:
        return tuple(service.uuid for service in self.services)

    def service_for(self, service_uuid: UUID) -> ServiceSpec | None:
        return self._by_service.get(service_uuid)

    def characteristic_for(self, characteristic_uuid: UUID) -> CharacteristicSpec | None:
        return self._by_characteristic.get(characteristic_uuid)


class ServerConnection:
    """One accepted client connection. This keeps framing and request handling per peer."""

    def __init__(
        self,
        server: WftnpServer,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.server = server
        self.reader = reader
        self.writer = writer
        self.subscriptions: set[UUID] = set()
        self.closed = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task = asyncio.create_task(self._read_loop(), name="wftnp-server-connection")

    async def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        self.writer.close()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        with contextlib.suppress(ConnectionError, OSError):
            await self.writer.wait_closed()

    async def _send_frame(
        self,
        opcode: Opcode,
        sequence: int,
        *,
        payload: bytes = b"",
        status: int = 0,
    ) -> None:
        async with self._lock:
            self.writer.write(encode(Frame(opcode, sequence, status, payload)))
            await self.writer.drain()

    async def _send_error(self, opcode: Opcode, sequence: int) -> None:
        # The reverse-engineered protocol does not define a richer status vocabulary in
        # this repository; any nonzero status is interpreted as rejected by the client.
        await self._send_frame(opcode, sequence, payload=b"", status=1)

    async def _read_loop(self) -> None:
        try:
            while not self.closed.is_set():
                data = await self.reader.readexactly(6)
                try:
                    header = decode_header(data)
                except WftnpError:
                    # A malformed header is not actionable: we cannot safely reply with a
                    # valid opcode and sequence, so we close the peer.
                    break
                if header.status != 0:
                    await self._send_error(header.opcode, header.sequence)
                    continue
                payload = await self.reader.readexactly(header.length)
                request = Frame(header.opcode, header.sequence, header.status, payload)
                await self._handle_request(request)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            await self.server._detach_connection(self)
            self.closed.set()
            self.writer.close()

    async def _handle_request(self, request: Frame) -> None:
        if request.opcode not in (
            Opcode.SERVICES,
            Opcode.CHARACTERISTICS,
            Opcode.READ,
            Opcode.WRITE,
            Opcode.SUBSCRIBE,
        ):
            await self._send_error(request.opcode, request.sequence)
            return

        try:
            if request.opcode == Opcode.SERVICES:
                if request.payload:
                    raise ProtocolError("SERVICES requests must not include a payload")
                response = b"".join(service.uuid.bytes for service in self.server.registry.services)
                await self._send_frame(Opcode.SERVICES, request.sequence, payload=response)
                return

            if request.opcode == Opcode.CHARACTERISTICS:
                if len(request.payload) != 16:
                    raise ProtocolError("CHARACTERISTICS request payload must be exactly one UUID")
                service_uuid = UUID(bytes=request.payload)
                service = self.server.registry.service_for(service_uuid)
                if service is None:
                    raise ProtocolError(f"Unknown service UUID {service_uuid}")
                response = b"".join(
                    characteristic.uuid.bytes + bytes((int(characteristic.properties) & 0xFF,))
                    for characteristic in service.characteristics
                )
                await self._send_frame(Opcode.CHARACTERISTICS, request.sequence, payload=response)
                return

            if request.opcode == Opcode.READ:
                if len(request.payload) != 16:
                    raise ProtocolError("READ request payload must be exactly one UUID")
                characteristic_uuid = UUID(bytes=request.payload)
                characteristic = self.server.registry.characteristic_for(characteristic_uuid)
                if characteristic is None or not (characteristic.properties & CharacteristicProperties.READ):
                    raise ProtocolError(f"Characteristic {characteristic_uuid} is not readable")
                response = characteristic_uuid.bytes + characteristic.value
                await self._send_frame(Opcode.READ, request.sequence, payload=response)
                return

            if request.opcode == Opcode.WRITE:
                if len(request.payload) < 16:
                    raise ProtocolError("WRITE request payload must include a UUID and data")
                characteristic_uuid = UUID(bytes=request.payload[:16])
                value = request.payload[16:]
                characteristic = self.server.registry.characteristic_for(characteristic_uuid)
                if characteristic is None or not (characteristic.properties & CharacteristicProperties.WRITE):
                    raise ProtocolError(f"Characteristic {characteristic_uuid} is not writable")
                if characteristic.on_write is not None:
                    result = characteristic.on_write(value)
                    if inspect.isawaitable(result):
                        result = await result
                    if result is not None:
                        characteristic.value = result
                else:
                    characteristic.value = value
                await self._send_frame(Opcode.WRITE, request.sequence, payload=characteristic_uuid.bytes)
                return

            if request.opcode == Opcode.SUBSCRIBE:
                if len(request.payload) != 17:
                    raise ProtocolError("SUBSCRIBE request payload must be UUID + enable flag")
                characteristic_uuid = UUID(bytes=request.payload[:16])
                enable = request.payload[16]
                if enable not in (0, 1):
                    raise ProtocolError("SUBSCRIBE enable flag must be 0 or 1")
                characteristic = self.server.registry.characteristic_for(characteristic_uuid)
                if characteristic is None:
                    raise ProtocolError(f"Unknown characteristic UUID {characteristic_uuid}")
                if enable:
                    if not (characteristic.properties & CharacteristicProperties.NOTIFY):
                        raise ProtocolError(
                            f"Characteristic {characteristic_uuid} does not support notifications"
                        )
                    self.subscriptions.add(characteristic_uuid)
                    self.server._register_subscription(self, characteristic_uuid)
                else:
                    self.subscriptions.discard(characteristic_uuid)
                    self.server._unregister_subscription(self, characteristic_uuid)
                await self._send_frame(Opcode.SUBSCRIBE, request.sequence, payload=characteristic_uuid.bytes)
                return
        except ProtocolError:
            await self._send_error(request.opcode, request.sequence)
            return


class WftnpServer:
    """Minimal WFTNP server that exposes a configurable in-memory service registry."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 36866,
        services: Iterable[ServiceSpec] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.registry = _ServerRegistry(services or ())
        self._server: asyncio.Server | None = None
        self._connections: set[ServerConnection] = set()
        self._subscriptions: dict[UUID, set[ServerConnection]] = {}
        self._started = asyncio.Event()

    @property
    def endpoint(self) -> Endpoint:
        return Endpoint(self.host, self.port)

    @property
    def connections(self) -> tuple[ServerConnection, ...]:
        return tuple(sorted(self._connections, key=id))

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._accept, self.host, self.port)
        sock = self._server.sockets[0]
        if self.port == 0:
            self.port = sock.getsockname()[1]
        self._started.set()

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        tasks = tuple(connection.close() for connection in tuple(self._connections))
        await asyncio.gather(*tasks, return_exceptions=True)
        self._subscriptions.clear()
        self._connections.clear()
        self._started.clear()

    async def wait_ready(self, timeout: float = 5.0) -> None:
        try:
            async with asyncio.timeout(timeout):
                await self._started.wait()
        except TimeoutError as exc:
            raise TimeoutError("WFTNP server did not start in time") from exc

    async def _accept(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        connection = ServerConnection(self, reader, writer)
        self._connections.add(connection)

    async def _detach_connection(self, connection: ServerConnection) -> None:
        self._connections.discard(connection)
        for characteristic_uuid in tuple(connection.subscriptions):
            self._unregister_subscription(connection, characteristic_uuid)
        connection.subscriptions.clear()

    def _register_subscription(self, connection: ServerConnection, characteristic_uuid: UUID) -> None:
        self._subscriptions.setdefault(characteristic_uuid, set()).add(connection)

    def _unregister_subscription(self, connection: ServerConnection, characteristic_uuid: UUID) -> None:
        subscribers = self._subscriptions.get(characteristic_uuid)
        if subscribers is None:
            return
        subscribers.discard(connection)
        if not subscribers:
            self._subscriptions.pop(characteristic_uuid, None)

    async def notify_characteristic(self, characteristic_uuid: UUID, value: bytes) -> None:
        subscribers = tuple(self._subscriptions.get(characteristic_uuid, ()))
        for subscriber in subscribers:
            await subscriber._send_frame(Opcode.NOTIFY, 0, payload=characteristic_uuid.bytes + value)


__all__ = [
    "CharacteristicSpec",
    "ServiceSpec",
    "WftnpServer",
]
