"""WFTNP server mDNS lifecycle and real registration tests."""

import asyncio
import gc
import sys
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from wftnp.server import ServiceSpec, WftnpServer


@pytest.mark.asyncio
async def test_advertisement_starts_after_tcp_and_stops_before_close():
    service_uuid = uuid4()
    server = WftnpServer(
        host="127.0.0.1",
        port=0,
        services=[ServiceSpec(service_uuid)],
        mdns_name="Test Trainer",
        mdns_address="127.0.0.1",
    )

    async def check_start(advertisement):
        assert server._server is not None
        assert server.port > 0
        assert advertisement.service_uuids == (service_uuid,)

    with (
        patch("wftnp.server.ServerAdvertisement.start", new_callable=AsyncMock) as start,
        patch("wftnp.server.ServerAdvertisement.stop", new_callable=AsyncMock) as stop,
    ):
        await server.start()
        try:
            start.assert_awaited_once()
            assert server._advertisement is not None
            await check_start(server._advertisement)
        finally:
            await server.stop()
        stop.assert_awaited_once()
        assert server._server is None


@pytest.mark.asyncio
async def test_advertisement_failure_rolls_back_tcp():
    server = WftnpServer(
        host="127.0.0.1",
        port=0,
        mdns_name="Test Trainer",
        mdns_address="127.0.0.1",
    )
    with patch(
        "wftnp.server.ServerAdvertisement.start",
        new_callable=AsyncMock,
        side_effect=RuntimeError("mDNS failed"),
    ):
        with pytest.raises(RuntimeError, match="mDNS failed"):
            await server.start()
    assert server._server is None
    assert not server._started.is_set()


def test_mdns_requires_explicit_address():
    with pytest.raises(ValueError, match="mdns_address"):
        WftnpServer(mdns_name="Test Trainer")


@pytest.fixture

def event_loop_policy():
    # Windows Proactor UDP transports can outlive Zeroconf async_close().
    # Use Selector for this real multicast test, matching the POSIX lifecycle.
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest.mark.asyncio
async def test_wahoo_long_service_type_registers_with_real_zeroconf(event_loop_policy):
    """The Wahoo DNS-SD service label exceeds RFC 6335's 15-byte limit."""
    from wftnp.advertisement import ServerAdvertisement
    from wftnp.discovery import SERVICE_TYPE

    assert SERVICE_TYPE == "_wahoo-fitness-tnp._tcp.local."
    advertisement = ServerAdvertisement(name="FortiusANT Test", address="127.0.0.1", port=36866)
    await advertisement.start()
    try:
        assert advertisement._info is not None
        assert advertisement._info.type == SERVICE_TYPE
    finally:
        await advertisement.stop()
        # Windows asyncio UDP transports may finalize after async_close().
        # Flush pending callbacks and collect closed sockets before pytest
        # checks unraisable ResourceWarnings in the next test.
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
