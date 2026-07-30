"""Tests for the radar weather-severity slippery accel gate."""

from openpilot.selfdrive.controls.lib.longitudinal_planner import (
    WEATHER_ACCEL_SCALE,
    _apply_weather_severity_limit,
)


def test_clear_no_limit():
    assert _apply_weather_severity_limit(2.0, 0) == 2.0


def test_each_level_steps_down():
    accels = [_apply_weather_severity_limit(2.0, level) for level in range(4)]
    assert accels[0] > accels[1] > accels[2] > accels[3]
    for level, expected in enumerate(accels):
        assert expected == 2.0 * WEATHER_ACCEL_SCALE[level]


def test_heavy_hits_floor():
    assert _apply_weather_severity_limit(2.0, 3) == 2.0 * 0.4


def test_out_of_range_level_clamped():
    assert _apply_weather_severity_limit(2.0, 9) == 2.0 * WEATHER_ACCEL_SCALE[3]
    assert _apply_weather_severity_limit(2.0, -1) == 2.0
