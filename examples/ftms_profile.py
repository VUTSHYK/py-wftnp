"""Transport-independent FTMS indoor bike data encoder.

This is an application-layer example, not part of the WFTNP wire protocol.
Bluetooth SIG FTMS Indoor Bike Data (0x2AD2), little-endian.
"""

from __future__ import annotations

from dataclasses import dataclass
from struct import pack
from uuid import UUID

FITNESS_MACHINE_SERVICE = UUID("00001826-0000-1000-8000-00805f9b34fb")
INDOOR_BIKE_DATA = UUID("00002ad2-0000-1000-8000-00805f9b34fb")


@dataclass(frozen=True, slots=True)
class IndoorBikeSample:
    speed_kmh: float
    cadence_rpm: float | None = None
    power_watts: int | None = None


def encode_indoor_bike_data(sample: IndoorBikeSample) -> bytes:
    """Encode speed, optional cadence and optional power as FTMS Indoor Bike Data.

    Bit 0 = 0 means instantaneous speed is present. Bit 2 adds cadence,
    bit 6 adds instantaneous power. Units: 0.01 km/h, 0.5 rpm, 1 W.
    """
    speed = round(sample.speed_kmh * 100)
    if not 0 <= speed <= 65535:
        raise ValueError("speed_kmh out of FTMS range")

    flags = 0
    payload = pack("<H", speed)
    if sample.cadence_rpm is not None:
        cadence = round(sample.cadence_rpm * 2)
        if not 0 <= cadence <= 65535:
            raise ValueError("cadence_rpm out of FTMS range")
        flags |= 1 << 2
        payload += pack("<H", cadence)
    if sample.power_watts is not None:
        if not -32768 <= sample.power_watts <= 32767:
            raise ValueError("power_watts out of FTMS range")
        flags |= 1 << 6
        payload += pack("<h", sample.power_watts)
    return pack("<H", flags) + payload
