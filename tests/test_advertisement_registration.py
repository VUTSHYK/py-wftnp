"""Regression coverage for Wahoo's long mDNS service type."""

from unittest.mock import AsyncMock

import zeroconf.asyncio

from wftnp.advertisement import ServerAdvertisement
from wftnp.discovery import SERVICE_TYPE


async def test_registration_uses_non_strict_service_type(monkeypatch):
    register = AsyncMock()
    unregister = AsyncMock()
    close = AsyncMock()

    class FakeZeroconf:
        def __init__(self):
            self.async_register_service = register
            self.async_unregister_service = unregister
            self.async_close = close

    monkeypatch.setattr(zeroconf.asyncio, "AsyncZeroconf", FakeZeroconf)
    advertisement = ServerAdvertisement(name="Tacx Vortex", address="127.0.0.1", port=36866)
    await advertisement.start()
    info = register.await_args.args[0]
    assert SERVICE_TYPE == "_wahoo-fitness-tnp._tcp.local."
    assert info.type == SERVICE_TYPE
    assert info.name == f"Tacx Vortex.{SERVICE_TYPE}"
    assert info.server == "Tacx-Vortex.local."
    assert info.port == 36866
    assert info.parsed_addresses() == ["127.0.0.1"]
    assert register.await_args.kwargs == {"strict": False}
    await advertisement.stop()
    unregister.assert_awaited_once_with(info)
    close.assert_awaited_once()
