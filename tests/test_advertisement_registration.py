"""Regression: Wahoo's long DNS-SD service type requires non-strict registration."""

import pytest
from zeroconf import BadTypeInNameException
from wftnp.advertisement import ServerAdvertisement
from wftnp.discovery import SERVICE_TYPE


@pytest.mark.asyncio
async def test_long_wahoo_service_type_registers_and_unregisters():
    assert SERVICE_TYPE == "_wahoo-fitness-tnp._tcp.local."
    advertisement = ServerAdvertisement(name="Tacx Vortex", address="127.0.0.1", port=36866)
    try:
        await advertisement.start()
        assert advertisement._info is not None
        assert advertisement._info.type == SERVICE_TYPE
    finally:
        await advertisement.stop()
