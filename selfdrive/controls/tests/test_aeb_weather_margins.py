"""Tests for radar4d weather-severity AEB margin scaling."""

from openpilot.selfdrive.controls.lib.aeb import AEB


def test_clear_is_baseline():
    aeb = AEB()
    aeb.apply_weather_margins(0)
    assert aeb.predictor.params.min_brake == -2.5
    assert aeb.predictor.params.reaction_time == 1.0
    assert aeb.braking.TTC_PARTIAL == 1.5
    assert aeb.braking.TTC_FULL == 0.8


def test_heavy_scales_all_margins():
    aeb = AEB()
    aeb.apply_weather_margins(3)
    assert aeb.predictor.params.min_brake == -2.5 * 0.65
    assert aeb.predictor.params.reaction_time == 1.5
    assert aeb.braking.TTC_PARTIAL == 1.5 * 1.5
    assert aeb.braking.TTC_FULL == 0.8 * 1.5


def test_levels_step_monotonically_safer():
    aeb = AEB()
    pairs = []
    for level in range(4):
        aeb.apply_weather_margins(level)
        pairs.append((aeb.predictor.params.min_brake, aeb.predictor.params.reaction_time,
                      aeb.braking.TTC_PARTIAL))
    # min_brake (negative) gets weaker, reaction and TTC thresholds grow
    for i in range(3):
        assert pairs[i][0] <= pairs[i + 1][0]
        assert pairs[i][1] <= pairs[i + 1][1]
        assert pairs[i][2] <= pairs[i + 1][2]


def test_back_to_clear_restores_baseline():
    aeb = AEB()
    aeb.apply_weather_margins(3)
    aeb.apply_weather_margins(0)
    assert aeb.predictor.params.min_brake == -2.5
    assert aeb.predictor.params.reaction_time == 1.0
    assert aeb.braking.TTC_PARTIAL == 1.5


def test_out_of_range_severity_clamped():
    aeb = AEB()
    aeb.apply_weather_margins(9)
    assert aeb.predictor.params.min_brake == -2.5 * 0.65
    aeb.apply_weather_margins(-1)
    assert aeb.predictor.params.min_brake == -2.5
