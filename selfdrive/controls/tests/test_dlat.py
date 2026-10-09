"""Tests for EOP DLAT (Dynamic Lateral Profile).

DLAT had zero test coverage before this file, despite gating automatic
lane-change initiation (desire_helper.py reuses calculate_lane_confidence())
and DLON's ACC/E2E mode coupling (dlon.py::detect_lane_confidence_trigger()
reads dlat_use_laneless) -- real vehicle-behavior-affecting outputs.

Two of DLAT's internal signals are dead code on this branch, confirmed by
checking cereal/log.capnp directly rather than assumed from the source:
ModelDataV2 has no predictedPath or predictedPathStd field, so
_calculate_path_deviation() always returns 0.0 and the path_confidence term
inside _calculate_model_confidence() always falls back to its 0.5 default.
In practice this means _update_auto_state()'s `force_laneless` reduces to
just `curve_detected` -- the path-deviation clause can never be true. Tests
below cover the effective (reachable) behavior, not the dead branches, and
note explicitly where dead code is being exercised anyway for regression
safety.
"""
import sys
from unittest.mock import MagicMock  # noqa: TID251

# Stub out missing Cython extensions before any imports (same pattern as
# test_dlon.py in this same directory).
_fake_params_pyx = MagicMock()
_fake_params_pyx.Params = MagicMock
_fake_params_pyx.ParamKeyFlag = MagicMock()
_fake_params_pyx.ParamKeyType = MagicMock()
_fake_params_pyx.UnknownKeyName = Exception
sys.modules['openpilot.common.params_pyx'] = _fake_params_pyx

_fake_msgq = MagicMock()
_fake_msgq.Context = MagicMock
_fake_msgq.Poller = MagicMock
_fake_msgq.SubSocket = MagicMock
_fake_msgq.PubSocket = MagicMock
_fake_msgq.SocketEventHandle = MagicMock
_fake_msgq.toggle_fake_events = MagicMock()
_fake_msgq.fake_event_callback = MagicMock()
_fake_msgq.async_sleep = MagicMock()
_fake_msgq.async_wait_for_one_event = MagicMock()
_fake_msgq.MAX_FDS = 64
sys.modules['msgq.ipc_pyx'] = _fake_msgq

import time

from openpilot.selfdrive.controls.lib.dlat import DLAT, DLATMode, DLATState


def _make_model_v2(lane_line_probs=(0.9, 0.9, 0.9, 0.9), orientation_rate_z=(0.0,) * 5):
  mv = MagicMock(spec=['laneLineProbs', 'orientationRate'])
  mv.laneLineProbs = list(lane_line_probs)
  mv.orientationRate.z = list(orientation_rate_z)
  # Real ModelDataV2 has no predictedPath/predictedPathStd. laneLines is
  # omitted from this mock's spec too: production does have it, but
  # _calculate_path_deviation() returns 0.0 regardless once predictedPath is
  # absent (laneless_path stays None either way), so the result is identical
  # either way -- this just avoids needing a realistic laneLines fixture.
  # `spec=` makes hasattr() false for anything not listed above.
  return mv


def _make_car_state(v_ego=10.0):
  cs = MagicMock()
  cs.vEgo = v_ego
  return cs


def _new_dlat(curve_assist_enabled=True):
  dlat = DLAT()
  # Bypass the params mock entirely, matching test_dlon.py's pattern of
  # overriding cached-param attributes directly in setup.
  dlat._curve_assist_enabled = curve_assist_enabled
  dlat._load_params = lambda: None
  return dlat


class TestLaneConfidence:
  """calculate_lane_confidence is a static method reused by desire_helper.py's
  LCA initiation gate -- test it standalone, not just through DLAT.update()."""

  def test_missing_model_returns_neutral_default(self):
    assert DLAT.calculate_lane_confidence(None) == 0.5

  def test_model_without_lane_line_probs_returns_neutral_default(self):
    mv = MagicMock(spec=[])
    assert DLAT.calculate_lane_confidence(mv) == 0.5

  def test_too_few_lane_probs_returns_neutral_default(self):
    mv = _make_model_v2(lane_line_probs=(0.9, 0.9))
    assert DLAT.calculate_lane_confidence(mv) == 0.5

  def test_all_high_probs_gives_full_confidence(self):
    mv = _make_model_v2(lane_line_probs=(1.0, 1.0, 1.0, 1.0))
    assert DLAT.calculate_lane_confidence(mv) == 1.0

  def test_inner_lines_weighted_higher_than_outer(self):
    # Only inner lines confident (weights 0.4 each) -> 0.8, not 0.5.
    mv = _make_model_v2(lane_line_probs=(0.0, 1.0, 1.0, 0.0))
    assert DLAT.calculate_lane_confidence(mv) == 0.8


class TestModelConfidence:
  def test_path_confidence_term_uses_fallback_since_predictedPathStd_absent(self):
    # Real ModelDataV2 never exposes predictedPathStd (confirmed above), so
    # this always resolves to 0.6*lane_conf + 0.4*0.5 in production.
    dlat = _new_dlat()
    mv = _make_model_v2(lane_line_probs=(1.0, 1.0, 1.0, 1.0))
    confidence = dlat._calculate_model_confidence(mv)
    assert confidence == 0.6 * 1.0 + 0.4 * 0.5


class TestPathDeviation:
  """_calculate_path_deviation is confirmed dead code in production: real
  ModelDataV2 has no predictedPath field, so this always returns 0.0."""

  def test_none_model_returns_zero(self):
    dlat = _new_dlat()
    assert dlat._calculate_path_deviation(None) == 0.0

  def test_realistic_model_shape_returns_zero_since_predictedPath_is_absent(self):
    dlat = _new_dlat()
    mv = _make_model_v2()
    assert dlat._calculate_path_deviation(mv) == 0.0


class TestPredictCurve:
  def test_disabled_curve_assist_never_detects_curve_regardless_of_input(self):
    dlat = _new_dlat(curve_assist_enabled=False)
    mv = _make_model_v2(orientation_rate_z=(1.0, 1.0, 1.0, 1.0, 1.0))
    cs = _make_car_state(v_ego=1.0)
    assert dlat._predict_curve(mv, cs) is False

  def test_missing_orientation_rate_returns_false(self):
    dlat = _new_dlat()
    mv = MagicMock(spec=[])
    cs = _make_car_state()
    assert dlat._predict_curve(mv, cs) is False

  def test_empty_z_rates_returns_false(self):
    dlat = _new_dlat()
    mv = _make_model_v2(orientation_rate_z=())
    cs = _make_car_state()
    assert dlat._predict_curve(mv, cs) is False

  def test_curvature_above_threshold_detects_curve(self):
    dlat = _new_dlat()
    # rate_2s / speed > 0.055 -> curve. speed clamped to max(v_ego, 1.0).
    mv = _make_model_v2(orientation_rate_z=(0.0, 0.0, 0.0, 0.0, 0.5))
    cs = _make_car_state(v_ego=1.0)
    assert dlat._predict_curve(mv, cs) is True

  def test_curvature_below_threshold_does_not_detect_curve(self):
    dlat = _new_dlat()
    mv = _make_model_v2(orientation_rate_z=(0.0, 0.0, 0.0, 0.0, 0.01))
    cs = _make_car_state(v_ego=1.0)
    assert dlat._predict_curve(mv, cs) is False

  def test_speed_is_floored_to_one_mps_avoiding_division_blowup(self):
    dlat = _new_dlat()
    # v_ego=0 would divide by zero without the max(v_ego, 1.0) floor; same
    # rate at v_ego=0 and v_ego=1 must give the same (finite) result.
    mv = _make_model_v2(orientation_rate_z=(0.0, 0.0, 0.0, 0.0, 0.5))
    assert dlat._predict_curve(mv, _make_car_state(v_ego=0.0)) == \
      dlat._predict_curve(mv, _make_car_state(v_ego=1.0))


class TestAutoModeStateMachine:
  """Exercised through update(), the real integration point, matching how
  test_dlon.py tests its own state machine."""

  def test_high_confidence_no_curve_stays_laneful(self):
    dlat = _new_dlat()
    mv = _make_model_v2(lane_line_probs=(1.0, 1.0, 1.0, 1.0))
    use_laneless, _, state_str = dlat.update(mv, _make_car_state())
    assert use_laneless is False
    assert state_str == DLATState.laneful.name

  def test_curve_detected_forces_immediate_laneless_bypassing_hysteresis(self):
    dlat = _new_dlat()
    mv = _make_model_v2(lane_line_probs=(1.0, 1.0, 1.0, 1.0),
                         orientation_rate_z=(0.0, 0.0, 0.0, 0.0, 0.5))
    use_laneless, _, state_str = dlat.update(mv, _make_car_state(v_ego=1.0))
    assert use_laneless is True
    assert state_str == DLATState.laneless.name

  def test_curve_assist_disabled_does_not_force_immediate_laneless(self):
    dlat = _new_dlat(curve_assist_enabled=False)
    mv = _make_model_v2(lane_line_probs=(1.0, 1.0, 1.0, 1.0),
                         orientation_rate_z=(0.0, 0.0, 0.0, 0.0, 0.5))
    use_laneless, _, state_str = dlat.update(mv, _make_car_state(v_ego=1.0))
    assert use_laneless is False
    assert state_str == DLATState.laneful.name

  def test_brief_low_confidence_below_hysteresis_time_stays_laneful(self):
    dlat = _new_dlat()
    mv = _make_model_v2(lane_line_probs=(0.0, 0.0, 0.0, 0.0))
    use_laneless, _, state_str = dlat.update(mv, _make_car_state())
    assert use_laneless is False
    assert state_str == DLATState.laneful.name

  def test_sustained_low_confidence_transitions_laneful_to_evaluate_to_laneless(self):
    dlat = _new_dlat()
    mv = _make_model_v2(lane_line_probs=(0.0, 0.0, 0.0, 0.0))
    dlat.update(mv, _make_car_state())  # starts the low_confidence timer
    # Simulate LANEFUL_TO_LANELESS_TIME (1.0s) having elapsed.
    dlat.low_confidence_start = time.monotonic() - 1.1
    use_laneless, _, state_str = dlat.update(mv, _make_car_state())
    assert state_str == DLATState.evaluate.name
    assert use_laneless is False
    # Simulate the additional 1s required inside `evaluate`.
    dlat.state_entry_time = time.monotonic() - 1.1
    use_laneless, _, state_str = dlat.update(mv, _make_car_state())
    assert state_str == DLATState.laneless.name
    assert use_laneless is True

  def test_laneless_exit_requires_sustained_high_confidence(self):
    dlat = _new_dlat()
    dlat.state = DLATState.laneless
    dlat.state_entry_time = time.monotonic()  # just entered
    high_conf_mv = _make_model_v2(lane_line_probs=(1.0, 1.0, 1.0, 1.0))
    # Not enough time elapsed yet -> stays laneless.
    use_laneless, _, state_str = dlat.update(high_conf_mv, _make_car_state())
    assert state_str == DLATState.laneless.name
    assert use_laneless is True
    # Simulate LANELESS_TO_LANEFUL_TIME (2.0s) having elapsed.
    dlat.state_entry_time = time.monotonic() - 2.1
    use_laneless, _, state_str = dlat.update(high_conf_mv, _make_car_state())
    assert state_str == DLATState.laneful.name
    assert use_laneless is False

  def test_laneless_does_not_latch_when_curve_persists(self):
    """Regression test: _update_auto_state()'s laneless->laneful exit
    condition only checks lane_confidence, never curve_detected. A
    sustained, well-marked curve does not prevent exit once confidence
    recovers -- confirmed by reading _update_auto_state() directly rather
    than assumed. (nagaspilot/controls/ngp_dlat.py had exactly this
    latching bug during development on dev/NGP10 and was fixed to match
    this exact behavior.)"""
    dlat = _new_dlat()
    dlat.state = DLATState.laneless
    dlat.state_entry_time = time.monotonic() - 2.1
    mv_high_confidence_and_curve = _make_model_v2(
      lane_line_probs=(1.0, 1.0, 1.0, 1.0),
      orientation_rate_z=(0.0, 0.0, 0.0, 0.0, 0.5),  # still a curve
    )
    use_laneless, _, state_str = dlat.update(mv_high_confidence_and_curve, _make_car_state(v_ego=1.0))
    assert state_str == DLATState.laneful.name
    assert use_laneless is False

  def test_forced_laneful_mode_ignores_confidence_entirely(self):
    dlat = _new_dlat()
    dlat.mode = DLATMode.laneful
    mv = _make_model_v2(lane_line_probs=(0.0, 0.0, 0.0, 0.0))
    use_laneless, mode_str, state_str = dlat.update(mv, _make_car_state())
    assert use_laneless is False
    assert mode_str == "Laneful"
    assert state_str == "forced"

  def test_forced_laneless_mode_ignores_confidence_entirely(self):
    dlat = _new_dlat()
    dlat.mode = DLATMode.laneless
    mv = _make_model_v2(lane_line_probs=(1.0, 1.0, 1.0, 1.0))
    use_laneless, mode_str, state_str = dlat.update(mv, _make_car_state())
    assert use_laneless is True
    assert mode_str == "Laneless"
    assert state_str == "forced"


if __name__ == '__main__':
  import traceback
  failures = 0
  passed = 0
  for cls in (TestLaneConfidence, TestModelConfidence, TestPathDeviation, TestPredictCurve, TestAutoModeStateMachine):
    instance = cls()
    for name in dir(instance):
      if name.startswith('test_'):
        try:
          getattr(instance, name)()
          passed += 1
          print(f"  PASS: {cls.__name__}.{name}")
        except Exception:
          failures += 1
          print(f"  FAIL: {cls.__name__}.{name}")
          traceback.print_exc()
  msg = 'All passed' if failures == 0 else f'{failures} failure(s)'
  print(f'\n{passed} passed, {msg}')
  exit(0 if failures == 0 else 1)
