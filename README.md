# wftnp

An asynchronous, typed Python client for WFTNP (Wahoo Direct Connect).

The library discovers endpoints, inspects services and characteristics, exchanges
opaque characteristic bytes, and receives notifications. It manages connections
and restores subscriptions after network interruptions. It does not interpret
fitness metrics, control workouts, or depend on Home Assistant.

Requires Python 3.11 or newer. The core client has no runtime dependencies.

```sh
pip install wftnp
# Optional mDNS discovery:
pip install 'wftnp[discovery]'
```

```python
import asyncio
from wftnp import Endpoint, WftnpClient


async def main():
    async with WftnpClient(Endpoint("trainer.local", 36866)) as client:
        for service in await client.discover_services():
            print(service.uuid)
            for characteristic in await client.discover_characteristics(service.uuid):
                print(characteristic.uuid, characteristic.properties)


asyncio.run(main())
```

See [architecture and lifecycle](docs/architecture.md) for the behavioral contract,
and [examples](examples/) for notification streaming and read-only hardware checks.

## Notifications

```python
from uuid import UUID


async def stream(client: WftnpClient, characteristic: UUID):
    async with await client.subscribe(characteristic) as subscription:
        async for notification in subscription:
            print(notification.connection_id, notification.value.hex())
```

Subscriptions recover across reconnects. Requests and writes are never replayed.
Disconnected calls fail promptly; use `await client.wait_ready()` when recovery should
be awaited. Notification buffers are bounded and report overflow explicitly.
Values remain raw bytes: decoding speed, cadence, power, or treadmill metrics belongs
in a fitness-profile layer above this library.

## Discovery

```python
from wftnp.discovery import discover


async def find_devices():
    for advertisement in await discover(timeout=3):
        print(advertisement.name, advertisement.endpoint)
```

## WFTNP server advertisement (opt-in)

The TCP server can advertise itself on the LAN using mDNS when the optional
`discovery` dependency is installed. Advertising starts only after the TCP
listener is ready and is removed before the listener stops. The address must
be a reachable interface address; do not use `0.0.0.0` or `::`.

```python
from wftnp.server import WftnpServer

server = WftnpServer(
    host="0.0.0.0",
    port=36866,
    mdns_name="FortiusANT Trainer",
    mdns_address="192.168.1.100",  # Replace with the Raspberry Pi LAN address.
)
await server.start()
# ...
await server.stop()
```

This advertises `_wahoo-fitness-tnp._tcp.local.`. No advertisement is
published unless `mdns_name` is configured. WFTNP service metadata is derived
from the configured service registry. Discovery alone does not implement the
FTMS profile or guarantee that Zwift recognizes the trainer.

## Development

```sh
uv sync --all-extras
task check
task build
```

Tests use a local TCP simulator and require no hardware. Real-device checks are
explicitly opted into and perform only discovery, reads, and notification subscriptions.

For the single serial hardware lane, copy `hardware.example.toml` to `.hardware.toml`,
fill in the device endpoints, and run `task test:hardware`. The configuration and
JUnit report are gitignored. Normal tests and hosted CI never connect to equipment.
See [hardware validation](docs/hardware-validation.md) for scope and results.

## Project background and acknowledgements

This repository builds on the original [binarylogic/py-wftnp](https://github.com/binarylogic/py-wftnp)
Python WFTNP client library. We extended that foundation with **WFTNP TCP server
functionality**, allowing Python applications to expose services and
characteristics over a network, including FTMS-compatible fitness services.

The server extensions were developed for
[FortiusANT](https://github.com/VUTSHYK/FortiusANT), with the aim of making
Tacx trainers accessible to compatible fitness applications over WFTNP.
This avoids requiring Bluetooth or ANT+ for the *application-facing* link;
the trainer-side connection may still use ANT+.

In addition to the original client implementation, the server development drew
on the publicly available reverse-engineered protocol documentation in
[elfrances/wahoo-fitness-tnp](https://github.com/elfrances/wahoo-fitness-tnp).
That research helped inform protocol framing, message structures, discovery,
characteristic operations, and notifications.

**Credits:**
- [binarylogic/py-wftnp](https://github.com/binarylogic/py-wftnp) — original
  Python library and client-side foundation.
- [elfrances/wahoo-fitness-tnp](https://github.com/elfrances/wahoo-fitness-tnp) —
  publicly shared reverse-engineered WFTNP protocol information.

This is an independent, unofficial implementation, not affiliated with or
endorsed by Wahoo Fitness. Automated TCP tests do not establish compatibility
with every real Wahoo device or third-party application.

## Protocol basis

WFTNP is a vendor-originated protocol with an unofficial public description:
[elfrances/wahoo-fitness-tnp](https://github.com/elfrances/wahoo-fitness-tnp).
This implementation is independently written. It is not affiliated with Wahoo.
Characteristic payload semantics belong to higher-level libraries.
