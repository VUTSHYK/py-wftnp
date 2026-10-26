"""WFTNP server mDNS lifecycle without network multicast in CI."""

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
