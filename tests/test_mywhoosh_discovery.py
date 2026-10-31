"""MyWhoosh DNS-SD compatibility regression tests.

MyWhoosh reportedly normalizes spaces in the DNS-SD instance label to
hyphens and compares that label with the SRV target hostname.
These tests validate the ServiceInfo we actually register, not a live
multicast packet capture.
"""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import zeroconf.asyncio

from wftnp.advertisement import ServerAdvertisement
from wftnp.discovery import SERVICE_TYPE


@pytest.mark.parametrize(
    ("name", "target"),
    [
        ("Tacx Vortex", "Tacx-Vortex.local."),
        ("Wahoo KICKR 1234", "Wahoo-KICKR-1234.local."),
        ("FortiusANT", "FortiusANT.local."),
        ("Tacx Vortex #1", "Tacx-Vortex--1.local."),
    ],
)
async def test_mywhoosh_instance_matches_srv_target(monkeypatch, name, target):
    register = AsyncMock()
    unregister = AsyncMock()
    close = AsyncMock()

    class FakeZeroconf:
        def __init__(self):
            self.async_register_service = register
            self.async_unregister_service = unregister
            self.async_close = close

    monkeypatch.setattr(zeroconf.asyncio, "AsyncZeroconf", FakeZeroconf)
    ad = ServerAdvertisement(
        name=name,
        address="192.0.2.45",
        port=36866,
        service_uuids=[
            UUID("00001826-0000-1000-8000-00805f9b34fb"),
            UUID("0000180d-0000-1000-8000-00805f9b34fb"),
        ],
        serial_number="vortex-001",
        mac_address="02:00:00:00:00:01",
    )
    await ad.start()
    try:
        info = register.await_args.args[0]
        assert info.type == SERVICE_TYPE
        assert info.name == f"{name}.{SERVICE_TYPE}"
        assert info.server == target
        assert info.server == info.name.removesuffix(SERVICE_TYPE).rstrip(".").replace(" ", "-") + ".local."
        assert info.port == 36866
        assert info.parsed_addresses() == ["192.0.2.45"]
        assert info.properties[b"serial-number"] == b"vortex-001"
        assert info.properties[b"mac-address"] == b"02:00:00:00:00:01"
        assert info.properties[b"ble-service-uuids"] == (
            b"00001826-0000-1000-8000-00805f9b34fb,"
            b"0000180d-0000-1000-8000-00805f9b34fb"
        )
    finally:
        await ad.stop()
    unregister.assert_awaited_once()
    close.assert_awaited_once()
