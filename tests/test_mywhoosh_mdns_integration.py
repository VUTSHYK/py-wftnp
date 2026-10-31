"""End-to-end DNS-SD advertisement/resolution with real Zeroconf sockets.

Unlike mocked registration tests, this resolves the advertised service
through a separate Zeroconf instance. CI networking must permit local mDNS
multicast; a failure can indicate runner network restrictions.
"""

import asyncio
from uuid import UUID

import pytest
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from wftnp.advertisement import ServerAdvertisement
from wftnp.discovery import SERVICE_TYPE


@pytest.mark.asyncio
async def test_mywhoosh_advertisement_resolves_over_mdns():
    name = "Tacx Vortex MyWhoosh Test"
    address = "127.0.0.1"
    port = 36866
    service_name = f"{name}.{SERVICE_TYPE}"
    advertiser = ServerAdvertisement(
        name=name,
        address=address,
        port=port,
        service_uuids=(UUID("00001826-0000-1000-8000-00805f9b34fb"),),
        serial_number="mywhoosh-test",
        mac_address="02:00:00:00:00:01",
    )
    resolver = AsyncZeroconf()
    try:
        await advertiser.start()
        info = ServiceInfo(SERVICE_TYPE, service_name)
        found = False
        for _ in range(10):
            if await info.async_request(resolver.zeroconf, timeout=1000):
                found = True
                break
            await asyncio.sleep(0.2)
        assert found, "Separate Zeroconf resolver could not resolve mDNS service"
        assert info.name == service_name
        assert info.server == "Tacx-Vortex-MyWhoosh-Test.local."
        assert info.port == port
        assert info.parsed_addresses() == [address]
        assert info.properties[b"ble-service-uuids"] == b"00001826-0000-1000-8000-00805f9b34fb"
        assert info.properties[b"serial-number"] == b"mywhoosh-test"
        assert info.properties[b"mac-address"] == b"02:00:00:00:00:01"
    finally:
        await advertiser.stop()
        await resolver.async_close()
