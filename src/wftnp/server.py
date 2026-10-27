"""WFTNP server transport support. The generic protocol remains byte-oriented."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

from .advertisement import ServerAdvertisement
from .exceptions import ProtocolError, WftnpError
from .models import CharacteristicProperties, Endpoint
from .protocol import HEADER, Frame, Opcode, decode_header, encode


class ResponseCode(IntEnum):
    SUCCESS = 0
    INVALID_MESSAGE_TYPE = 1
    GENERIC_ERROR = 2
    SERVICE_NOT_FOUND = 3
    CHARACTERISTIC_NOT_FOUND = 4
    OPERATION_NOT_SUPPORTED = 5
    CHARACTERISTIC_WRITE_FAILED = 6


@dataclass(slots=True)
class CharacteristicSpec:
    """Uninterpreted GATT-like characteristic value and access policy."""

    uuid: UUID
    properties: CharacteristicProperties | int = CharacteristicProperties.READ
    value: bytes = b""
    on_write: Callable[[bytes], bytes | Awaitable[bytes] | None] | None = None
    on_write_with_connection: Callable[[ServerConnection, bytes], bytes | Awaitable[bytes] | None] | None = (
        None
    )

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
        self._deferred_write_notifications: list[tuple[UUID, bytes]] | None = None
        self.closed = asyncio.Event()
        self._lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._task = asyncio.create_task(self._read_loop(), name="wftnp-server-connection")

    async def close(self) -> None:
        async with self._close_lock:
            self.closed.set()
            self.writer.close()
            if self._task is not asyncio.current_task():
                if not self._task.done():
                    self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
            with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                async with asyncio.timeout(self.server.write_timeout):
                    await self.writer.wait_closed()

    async def _send_frame(
        self,
        opcode: Opcode | int,
        sequence: int,
        *,
        payload: bytes = b"",
        status: int = 0,
    ) -> None:
        async with asyncio.timeout(self.server.write_timeout):
            async with self._lock:
                if self.closed.is_set():
                    raise ConnectionError("WFTNP connection is closed")
                self.writer.write(encode(Frame(opcode, sequence, status, payload)))
                await self.writer.drain()

    async def _send_error(
        self, opcode: Opcode | int, sequence: int, code: ResponseCode = ResponseCode.GENERIC_ERROR
    ) -> None:
        await self._send_frame(opcode, sequence, payload=b"", status=int(code))

    async def _read_loop(self) -> None:
        try:
            while not self.closed.is_set():
                async with asyncio.timeout(self.server.idle_timeout):
                    data = await self.reader.readexactly(6)
                # Preserve the raw opcode so unsupported message types can be rejected
                # with the protocol-defined INVALID_MESSAGE_TYPE response.
                version, raw_opcode, sequence, _status, _length = HEADER.unpack(data)
                if version == 1 and raw_opcode not in Opcode._value2member_map_:
                    await self._send_error(raw_opcode, sequence, ResponseCode.INVALID_MESSAGE_TYPE)
                    break
                try:
                    header = decode_header(data)
                except WftnpError:
                    # A malformed header is not actionable: we cannot safely reply with a
                    # valid opcode and sequence, so we close the peer.
                    break
                if header.status != 0:
                    await self._send_error(header.opcode, header.sequence)
                    continue
                async with asyncio.timeout(self.server.idle_timeout):
                    payload = await self.reader.readexactly(header.length)
                request = Frame(header.opcode, header.sequence, header.status, payload)
                await self._handle_request(request)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, TimeoutError):
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
            await self._send_error(request.opcode, request.sequence, ResponseCode.INVALID_MESSAGE_TYPE)
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
                    await self._send_error(request.opcode, request.sequence, ResponseCode.SERVICE_NOT_FOUND)
                    return
                response = service_uuid.bytes + b"".join(
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
                if characteristic is None:
                    await self._send_error(
                        request.opcode, request.sequence, ResponseCode.CHARACTERISTIC_NOT_FOUND
                    )
                    return
                if not (characteristic.properties & CharacteristicProperties.READ):
                    await self._send_error(
                        request.opcode, request.sequence, ResponseCode.OPERATION_NOT_SUPPORTED
                    )
                    return
                response = characteristic_uuid.bytes + characteristic.value
                await self._send_frame(Opcode.READ, request.sequence, payload=response)
                return

            if request.opcode == Opcode.WRITE:
                if len(request.payload) < 16:
                    raise ProtocolError("WRITE request payload must include a UUID and data")
                characteristic_uuid = UUID(bytes=request.payload[:16])
                value = request.payload[16:]
                characteristic = self.server.registry.characteristic_for(characteristic_uuid)
                if characteristic is None:
                    await self._send_error(
                        request.opcode, request.sequence, ResponseCode.CHARACTERISTIC_NOT_FOUND
                    )
                    return
                if not (characteristic.properties & CharacteristicProperties.WRITE):
                    await self._send_error(
                        request.opcode, request.sequence, ResponseCode.OPERATION_NOT_SUPPORTED
                    )
                    return
                if characteristic.on_write is not None or characteristic.on_write_with_connection is not None:
                    self._deferred_write_notifications = []
                    try:
                        if characteristic.on_write_with_connection is not None:
                            result = characteristic.on_write_with_connection(self, value)
                        elif characteristic.on_write is not None:
                            result = characteristic.on_write(value)
                        else:
                            result = None
                        if inspect.isawaitable(result):
                            result = await result
                    except Exception:
                        self._deferred_write_notifications = None
                        await self._send_error(
                            request.opcode, request.sequence, ResponseCode.CHARACTERISTIC_WRITE_FAILED
                        )
                        return
                    if result is not None:
                        characteristic.value = result
                else:
                    characteristic.value = value
                pending = self._deferred_write_notifications
                self._deferred_write_notifications = None
                await self._send_frame(Opcode.WRITE, request.sequence, payload=characteristic_uuid.bytes)
                if pending:
                    for notify_uuid, notify_value in pending:
                        await self.server._deliver_notification(self, notify_uuid, notify_value)
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
                    await self._send_error(
                        request.opcode, request.sequence, ResponseCode.CHARACTERISTIC_NOT_FOUND
                    )
                    return
                if enable:
                    if not (characteristic.properties & CharacteristicProperties.NOTIFY):
                        await self._send_error(
                            request.opcode, request.sequence, ResponseCode.OPERATION_NOT_SUPPORTED
                        )
                        return
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
        write_timeout: float = 2.0,
        max_pending_notifications: int = 64,
        max_connections: int | None = None,
        idle_timeout: float = 3600.0,
        on_disconnect: Callable[[], None | Awaitable[None]] | None = None,
        on_disconnect_with_connection: Callable[[ServerConnection], None | Awaitable[None]] | None = None,
        mdns_name: str | None = None,
        mdns_address: str | None = None,
        mdns_serial_number: str | None = None,
        mdns_mac_address: str | None = None,
    ) -> None:
        if max_connections is not None and max_connections < 1:
            raise ValueError("max_connections must be positive")
        if idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive")
        if write_timeout <= 0 or max_pending_notifications < 1:
            raise ValueError("write_timeout must be positive and notification limit at least one")
        if mdns_name is not None and mdns_address is None:
            raise ValueError("mdns_address is required when mdns_name is set")
        self._mdns_name = mdns_name
        self._mdns_address = mdns_address
        self._mdns_serial_number = mdns_serial_number
        self._mdns_mac_address = mdns_mac_address
        self._advertisement: ServerAdvertisement | None = None
        self.max_connections = max_connections
        self.idle_timeout = idle_timeout
        self.on_disconnect = on_disconnect
        self.on_disconnect_with_connection = on_disconnect_with_connection
        self.write_timeout = write_timeout
        self.host = host
        self.port = port
        self.registry = _ServerRegistry(services or ())
        self._server: asyncio.Server | None = None
        self._connections: set[ServerConnection] = set()
        self._subscriptions: dict[UUID, set[ServerConnection]] = {}
        self._started = asyncio.Event()
        self._stopping = False
        self._notify_tasks: set[asyncio.Task[None]] = set()
        self._notify_slots = asyncio.Semaphore(max_pending_notifications)
        self._notify_calls: set[asyncio.Task[None]] = set()

    @property
    def endpoint(self) -> Endpoint:
        return Endpoint(self.host, self.port)

    @property
    def connections(self) -> tuple[ServerConnection, ...]:
        return tuple(sorted(self._connections, key=id))

    async def start(self) -> None:
        if self._server is not None:
            return
        self._stopping = False
        self._server = await asyncio.start_server(self._accept, self.host, self.port)
        sock = self._server.sockets[0]
        if self.port == 0:
            self.port = sock.getsockname()[1]
        if self._mdns_name is not None and self._mdns_address is not None:
            advertisement = ServerAdvertisement(
                name=self._mdns_name,
                address=self._mdns_address,
                port=self.port,
                service_uuids=self.registry.service_uuids(),
                serial_number=self._mdns_serial_number,
                mac_address=self._mdns_mac_address,
            )
            try:
                await advertisement.start()
            except BaseException:
                self._server.close()
                await self._server.wait_closed()
                self._server = None
                raise
            self._advertisement = advertisement
        self._started.set()

    async def stop(self) -> None:
        if self._server is None:
            return
        self._stopping = True
        advertisement = self._advertisement
        self._advertisement = None
        if advertisement is not None:
            await advertisement.stop()
        server = self._server
        server.close()
        # Python 3.12+ wait_closed() may wait for active client handlers.
        # Close the peers first; otherwise shutdown deadlocks on open sockets.
        pending = tuple(self._notify_tasks)
        for task in pending:
            task.cancel()
        calls = tuple(self._notify_calls)
        for task in calls:
            task.cancel()
        await asyncio.gather(*pending, *calls, return_exceptions=True)
        await asyncio.gather(
            *(connection.close() for connection in tuple(self._connections)),
            return_exceptions=True,
        )
        await server.wait_closed()
        self._server = None
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
        if self._stopping or self._server is None:
            writer.close()
            return
        if self.max_connections is not None and len(self._connections) >= self.max_connections:
            writer.close()
            return
        connection = ServerConnection(self, reader, writer)
        self._connections.add(connection)

    async def _detach_connection(self, connection: ServerConnection) -> None:
        was_connected = connection in self._connections
        self._connections.discard(connection)
        if was_connected and self.on_disconnect_with_connection is not None:
            result = self.on_disconnect_with_connection(connection)
            if inspect.isawaitable(result):
                await result
        if was_connected and self.on_disconnect is not None:
            result = self.on_disconnect()
            if inspect.isawaitable(result):
                await result
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

    async def _deliver_notification(
        self, subscriber: ServerConnection, characteristic_uuid: UUID, value: bytes
    ) -> None:
        try:
            if subscriber.closed.is_set() or characteristic_uuid not in subscriber.subscriptions:
                return
            await subscriber._send_frame(Opcode.NOTIFY, 0, payload=characteristic_uuid.bytes + value)
        except (ConnectionError, OSError, TimeoutError):
            await subscriber.close()
            await self._detach_connection(subscriber)

    async def notify_connection(
        self, connection: ServerConnection, characteristic_uuid: UUID, value: bytes
    ) -> None:
        """Notify one subscribed client, without broadcasting control responses."""
        if connection not in self._connections or self._stopping or self._server is None:
            return
        if (
            connection._deferred_write_notifications is not None
            and asyncio.current_task() is connection._task
        ):
            connection._deferred_write_notifications.append((characteristic_uuid, bytes(value)))
            return
        await self._deliver_notification(connection, characteristic_uuid, value)

    async def notify_characteristic(self, characteristic_uuid: UUID, value: bytes) -> None:
        if self._stopping or self._server is None:
            return
        caller = asyncio.current_task()
        if caller is not None:
            self._notify_calls.add(caller)
        subscribers = tuple(self._subscriptions.get(characteristic_uuid, ()))
        tasks: list[asyncio.Task[None]] = []
        try:
            for subscriber in subscribers:
                await self._notify_slots.acquire()
                if self._stopping:
                    self._notify_slots.release()
                    break
                task = asyncio.create_task(
                    self._deliver_notification(subscriber, characteristic_uuid, value),
                    name="wftnp-server-notify",
                )
                self._notify_tasks.add(task)
                task.add_done_callback(self._notification_done)
                tasks.append(task)
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            if not self._stopping:
                raise
        finally:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if caller is not None:
                self._notify_calls.discard(caller)

    def _notification_done(self, task: asyncio.Task[None]) -> None:
        self._notify_tasks.discard(task)
        self._notify_slots.release()
        if not task.cancelled():
            task.exception()  # Observe unexpected failures instead of leaking task exceptions.


__all__ = [
    "CharacteristicSpec",
    "ResponseCode",
    "ServiceSpec",
    "WftnpServer",
]
