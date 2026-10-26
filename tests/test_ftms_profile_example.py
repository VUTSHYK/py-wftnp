"""FTMS Indoor Bike Data example encoding regression tests."""

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "examples" / "ftms_profile.py"
SPEC = importlib.util.spec_from_file_location("ftms_profile_example", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_speed_only():
    assert module.encode_indoor_bike_data(module.IndoorBikeSample(25.5)) == bytes.fromhex(
        "0000f609"
    )


def test_speed_cadence_power():
    sample = module.IndoorBikeSample(36.0, 90.5, 250)
    assert module.encode_indoor_bike_data(sample) == bytes.fromhex("4400100eb500fa00")


@pytest.mark.parametrize(
    "sample",
    [
        (-1, None, None),
        (700, None, None),
        (10, -1, None),
        (10, 40000, None),
        (10, None, 40000),
    ],
)
def test_invalid_ranges(sample):
    with pytest.raises(ValueError):
        module.encode_indoor_bike_data(module.IndoorBikeSample(*sample))
