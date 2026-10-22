# WFTNP Server Implementation Plan

## Scope and objective

This repository is currently a typed asynchronous WFTNP client library. It intentionally implements the protocol in the client direction only: it connects to a WFTNP server, discovers services and characteristics, reads and writes opaque characteristic bytes, and manages subscription recovery. The project goal is to extend this library into a dual-role implementation that can also host a WFTNP/DIRCON server for a trainer bridge, while preserving the current client API.

This document is the Phase A audit outcome and the implementation plan for Phase B. No production code is modified in this phase.

## Audit summary

### Repository model

The current package is intentionally narrow and well-structured:

- `src/wftnp/protocol.py` contains the pure wire codec and all WFTNP framing validation.
- `src/wftnp/models.py` defines immutable transport protocol data classes such as `Endpoint`, `Service`, `Characteristic`, `Notification`, and `ClientState`.
- `src/wftnp/exceptions.py` defines explicit transport and protocol failure classes.
- `src/wftnp/connection.py` owns one TCP socket lifetime, serialized request dispatch, sequence tracking, response correlation, and reader loop.
- `src/wftnp/client.py` owns the public client operations, connection supervision, backoff/reconnect policy, and subscription lifecycle.
- `src/wftnp/subscription.py` owns bounded notification delivery and consumer lifecycle.
- `src/wftnp/discovery.py` provides optional mDNS discovery using Zeroconf; it is intentionally independent of the client transport.

This is a clean separation: protocol codec, connection handling, client lifecycle, and application-level fitness semantics are already split into modules.

### Current public API direction

The translated project description is explicit:

- This library is not a fitness profile parser.
- It does not interpret metrics or trainer commands.
- It is a generic WFTNP transport layer.
- The client is designed for a single event loop and remains intentionally protocol-focused.

That design is precisely the right baseline for a server implementation: transport and framing concerns are already isolated from profile logic.

## Reusable architecture and constraints

### Good reusable components

1. Wire protocol definition (`protocol.py`)
   - `Opcode` enum
   - `Frame` and `Header` dataclasses
   - `encode()` / `decode_header()` / `validate_response()`
   - UUID validation and payload length restrictions

2. Immutable protocol and lifecycle models (`models.py`)
   - `Endpoint`, `Service`, `Characteristic`, `Notification`, `CharacteristicProperties`
   - These are directly usable for a server-side GATT-like registry.

3. Exception model (`exceptions.py`)
   - `ProtocolError`, `OperationRejected`, `RequestTimeout`, `ConnectionLost`
   - The server will need a similar concept of invalid request, unsupported operation, or client disconnect.

4. Single-connection request lifecycle (`connection.py`)
   - The client-side connection logic already shows how sequence numbers, outstanding requests, and socket closure are handled.
   - A server-side `Connection` can reuse the same framing discipline, but with different rules for request handling, subscription state, and notification dispatch.

5. Subscription pattern (`subscription.py`)
   - The subscription design is not trainer-specific; it is a general queue + callback pattern.
   - The same idea can be adapted for server-side subscription bookkeeping.

### What is not reusable as-is

- `WftnpClient` is a client lifecycle supervisor with reconnect/backoff, not a server lifecycle manager.
- `Connection.open()` is TCP client connection logic; a server must accept new TCP listeners and handle multiple peer sockets concurrently.
- `client.py` has state machine semantics (`STOPPED -> CONNECTING -> RESTORING -> READY -> BACKOFF`) that represent the client-side network lifecycle and must not be used directly for a server.
- Subscription intent restoration is client-side recovery behavior and not meaningful for a server that owns the authoritative device state.

## Comparison with protocol reference

The protocol reference at `elfrances/wahoo-fitness-tnp` is a reverse-engineered unofficial WFTNP spec. The repo’s implementation appears to match the core protocol model used by Wahoo Direct Connect:

- 1-byte version field
- 1-byte operation code
- 1-byte request sequence id
- 1-byte status code
- 2-byte payload length
- wire format: `!BBBBH` header, followed by payload bytes
- UUID-based characteristic addressing
- response matching by opcode and sequence
- nonzero status values representing rejected operations

The existing implementation in `protocol.py` is already consistent with the core WFTNP framing model. The server must therefore implement the same on-wire framing, not a custom JSON or binary format.

Key server obligations derived from the protocol contract:

- Accept TCP connections and parse frames from multiple clients.
- Validate version, opcode, sequence, payload length, and UUID shape.
- Respond to `SERVICES` requests with a list of 16-byte UUIDs.
- Respond to `CHARACTERISTICS` requests with a list of 17-byte records: UUID (16 bytes) + property bitfield (1 byte).
- Respond to `READ` requests with `UUID || value` payload.
- Support `WRITE` requests with opaque bytes payloads.
- Support `SUBSCRIBE` requests with enable/disable semantics.
- Send unsolicited `NOTIFY` frames on active subscriptions.
- Return proper status codes for unsupported or invalid requests.

Important ambiguity to document:

- The reverse-engineered protocol reference provides many Wahoo-specific service and characteristic UUIDs, but not necessarily a complete public machine-validated contract for every trainer model.
- Characteristic payload semantics must remain outside the generic protocol library; they belong in a fitness profile layer.
- The generic server should not claim Wahoo identity or invent official support claims.

## Missing server functionality

The current library is missing all server-side primitives required for a WFTNP/DIRCON trainer bridge:

1. Listener lifecycle
   - No `asyncio.start_server()`-style server loop
   - No bind address/port configuration
   - No graceful shutdown for multiple client connections

2. GATT-like registry
   - No device/service/characteristic registry abstraction
   - No mapping from UUID to read/write/notify behavior
   - No configuration for static or dynamic characteristic values

3. Request dispatcher
   - No server-side request parser for all supported opcodes
   - No status-code selection logic for malformed or unsupported requests
   - No validation for sequence mismatch or invalid UUID lengths

4. Subscription manager
   - No per-client subscription state
   - No notification fan-out to one or many subscribed clients
   - No backpressure, overflow, or disconnect cleanup policy

5. Error handling
   - No handling of malformed packets, incomplete frames, invalid lengths, unsupported opcodes, or connection teardown
   - No cancellation-safe shutdown behavior for active subscriptions

6. Discovery and advertisement
   - Optional mDNS advertisement may be useful for network-visible trainers, but it is not mandatory for a transport library.
   - Should be separate and optional, not hard-wired into the core server module.

## Architectural recommendation

### Preferred module layout

Use a new module at `src/wftnp/server.py` as the primary server implementation.

Rationale:

- It preserves the existing `WftnpClient` API and keeps the package oriented around the same protocol transport semantics.
- It allows server behavior to be opt-in rather than forcing a server-capable runtime into all users.
- It keeps the RFC-like transport logic centralized and easy to test.
- It avoids mixing client recovery policy with server ownership semantics.

A separate package such as `src/wftnp_server/` is possible only if the project later grows a broader trainer profile or service layer. For the current scope, a single `wftnp.server` module is the cleanest and least disruptive approach.

### Core design building blocks

The server implementation should likely consist of:

- `CharacteristicRegistry`: stores service UUIDs and characteristic metadata + handlers
- `ServerConnection`: per-accepted TCP client, reads frames, writes frames, tracks sequence numbers, and dispatches requests
- `SubscriptionState`: per-client and per-characteristic active subscriptions
- `WftnpServer`: accepts socket connections, owns the registry, and orchestrates broadcast notifications

These types can be implemented in a single file initially to keep the first server patch reviewable, then split into additional modules if the API expands.

## Public API proposal

Keep the public client surface unchanged and add a minimally invasive server API:

```python
from uuid import UUID

from wftnp.server import (
    CharacteristicSpec,
    ServiceSpec,
    WftnpServer,
)

server = WftnpServer(
    host="0.0.0.0",
    port=36866,
    services=(
        ServiceSpec(
            uuid=UUID("00001826-0000-1000-8000-00805f9b34fb"),
            characteristics=(
                CharacteristicSpec(
                    uuid=UUID("00002ad2-0000-1000-8000-00805f9b34fb"),
                    properties={"read", "notify"},
                    read=lambda: b"value",
                ),
            ),
        ),
    ),
)

await server.start()
try:
    await server.wait_ready()
    ...
finally:
    await server.stop()
```

More advanced server API could include:

- `set_value(characteristic_uuid, value: bytes)`
- `notify(characteristic_uuid, value: bytes, *, target: str | None = None)`
- `subscribe(client_id, characteristic)`
- `unsubscribe(...)`
- `add_service(...)`, `add_characteristic(...)`

The public API should treat bytes as the protocol-level value and not require fitness profile encoders in the core layer.

## Tests to add in Phase B

The server should be validated with a local simulated client, not physical hardware.

### Required test areas

- valid service discovery response
- valid characteristic discovery response
- read request and read response payload formatting
- write request with valid opaque bytes
- write request with invalid payload length or malformed UUID
- notification subscriptions for a single client
- multi-client subscription fan-out
- subscription removal / unsubscribe behavior
- malformed frame lengths
- fragmented TCP writes and coalesced reads
- out-of-order sequence handling
- unsupported operation status response
- disconnect cleanup and cancellation
- server shutdown while clients remain connected
- backpressure and bounded queue behavior
- invalid opcode / invalid version / invalid response status

The existing local test server in `tests/support/server.py` is a useful model for writing deterministic wire-level tests. It should be reused or extended for the server-unit tests.

## Dependency, licensing, and platform analysis

### Python compatibility

The project currently requires Python 3.11 or newer, and CI covers 3.11-3.14 across Linux, macOS, and Windows. This is suitable for the server extension because asyncio and `asyncio.start_server()` are mature and available across all supported platforms.

### Dependency policy

The library has a zero-runtime-dependency core and only optional mDNS discovery support via `zeroconf`.

This is a strong choice for the server as well because:

- the protocol itself does not need extra packages;
- mDNS and advertising can remain optional and separate; and
- Raspberry Pi OS compatibility is improved by keeping the core dependency footprint minimal.

### Licensing

The project is licensed under Apache 2.0. This is compatible with a protocol implementation derived from an unofficial reverse-engineered reference as long as the project remains transparent about the source, does not falsely claim official endorsement, and keeps the attribution policy documented.

### Raspberry Pi OS considerations

The server implementation should be compatible with Raspberry Pi OS and other Linux-like systems:

- use standard asyncio socket APIs;
- avoid OS-specific dependencies;
- allow bind address `0.0.0.0` or a specific local address;
- keep the GATT-like registry and encoders pure and deterministic;
- do not rely on GUI libraries or Bluetooth stack integration in the protocol core.

It is appropriate to make mDNS/advertising optional and independent of the core server.

## Implementation stages

### Stage 1 — protocol-compatibility foundation

- Confirm the WFTNP frame parser and validator are reused exactly.
- Add a standalone server-side request decoder that handles `SERVICES`, `CHARACTERISTICS`, `READ`, `WRITE`, `SUBSCRIBE`, and `NOTIFY` semantics.
- Define response status codes and malformed request handling.

### Stage 2 — connection and lifecycle

- Implement `WftnpServer` with `start()` / `stop()` / `wait_ready()` semantics.
- Accept multiple clients and track per-connection state.
- Add graceful shutdown and cancellation handling.

### Stage 3 — registry and behavior

- Add an in-memory registry for services and characteristics.
- Provide both static and callback-based value sources.
- Keep bytes as the value representation; do not decode trainer metrics at the protocol layer.

### Stage 4 — notification dispatch

- Support active per-characteristic subscriptions.
- Send notifications on subscribed clients only.
- Handle disconnects, stale subscribers, and queue overflow gracefully.

### Stage 5 — optional discovery

- If desired, add mDNS advertisement as a separate helper or optional server component.
- Keep it independent of the core server tests.

### Stage 6 — docs and examples

- Add example server usage.
- Document the fact that fitness profile interpretation remains in an outer application layer.
- Explicitly mention that this is a WFTNP transport-compatible server, not an official Wahoo implementation.

## Acceptance criteria

The server implementation is acceptable when all of the following are true:

1. The existing client library remains compatible and its API remains unchanged.
2. A local simulated client can connect to the server and successfully perform discovery, read, write, and subscription operations.
3. The server sends correct wire-format frames and validates client requests according to the WFTNP protocol model.
4. Malformed frames, unsupported operations, shutdown, and disconnects are handled deterministically and without leaking tasks.
5. Multi-client subscription behavior works and notifications are scoped correctly.
6. Characteristic values remain raw bytes; profile-specific encoding is not placed into the core server.
7. The server has explicit documentation describing protocol ambiguities and non-certified status.
8. The optional mDNS discovery remains independent and does not affect the core server tests.

## Phase A conclusion

The project is already structured to accommodate a server side without destabilizing the client.

The most appropriate design is:

- keep the current client code untouched;
- add a new transport-focused server module such as `src/wftnp/server.py`;
- reuse the existing `protocol.py`, `models.py`, and exception model; and
- keep fitness-specific behavior outside the WFTNP core.

This approach matches the current architecture, respects the repository’s existing design boundaries, and keeps the implementation reviewable, testable, and suitable for Raspberry Pi deployment.

## Phase B reminder

Once the plan above is accepted, implement the server in small, reviewable steps with tests first for the protocol boundary, then feature additions, then docs/example updates. Keep the public client API unchanged and document any protocol ambiguities rather than inventing semantics.

This plan intentionally avoids claiming official Wahoo certification or any blurred distinction between the transport protocol and a proprietary trainer implementation layer.

# End of plan

This document is the audit and implementation plan for the WFTNP server extension. It does not change the current runtime behavior of the library; it only defines the architecture and acceptance criteria for the server phase.
