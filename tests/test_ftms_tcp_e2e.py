"""End-to-end FTMS Indoor Bike Data over WFTNP TCP.

The example encoder is intentionally application-layer code, not a WFTNP dependency.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from wftnp import (
    CharacteristicProperties,
    CharacteristicSpec,
    Endpoint,
    ServiceSpec,
    WftnpClient,
    WftnpServer,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ftms_profile.py"
SPEC = importlib.util.spec_from_file_location("ftms_tcp_example", EXAMPLE)
assert SPEC is not None and SPEC.loader is not None
ftms = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ftms
SPEC.loader.exec_module(ftms)


@pytest.mark.asyncio
async def test_ftms_telemetry_from_encoder_through_tcp_notification():
    initial = ftms.encode_indoor_bike_data(ftms.IndoorBikeSample(0.0, 0.0, 0))
    characteristic = CharacteristicSpec(
        ftms.INDOOR_BIKE_DATA,
        CharacteristicProperties.READ | CharacteristicProperties.NOTIFY,
        value=initial,
    )
    server = WftnpServer(
        host="127.0.0.1",
        port=0,
        services=[ServiceSpec(ftms.FITNESS_MACHINE_SERVICE, (characteristic,))],
    )
    await server.start()
    client = WftnpClient(Endpoint("127.0.0.1", server.port), heartbeat_interval=None)
    try:
        await client.start()
        await client.wait_ready(timeout=3)
        services = await client.discover_services()
        assert [service.uuid for service in services] == [ftms.FITNESS_MACHINE_SERVICE]
        characteristics = await client.discover_characteristics(ftms.FITNESS_MACHINE_SERVICE)
        assert [item.uuid for item in characteristics] == [ftms.INDOOR_BIKE_DATA]
        assert await client.read_characteristic(ftms.INDOOR_BIKE_DATA) == initial

        async with await client.subscribe(ftms.INDOOR_BIKE_DATA) as subscription:
            sample = ftms.IndoorBikeSample(36.0, 90.5, 250)
            packet = ftms.encode_indoor_bike_data(sample)
            assert packet == bytes.fromhex("4400100eb500fa00")
            characteristic.value = packet
            await server.notify_characteristic(ftms.INDOOR_BIKE_DATA, packet)
            notification = await asyncio.wait_for(anext(subscription), 3)
            assert notification.characteristic == ftms.INDOOR_BIKE_DATA
            assert notification.value == packet
            assert await client.read_characteristic(ftms.INDOOR_BIKE_DATA) == packet
    finally:
        await client.stop()
        await server.stop()
