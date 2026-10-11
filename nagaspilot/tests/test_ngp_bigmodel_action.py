import numpy as np
import pytest

from nagaspilot.controls.ngp_bigmodel_action import action_from_head, should_stop


def smooth(val, prev, tau):  # drive_helpers.smooth_value with dt = 0.05
  alpha = 1 - np.exp(-0.05 / tau) if tau > 0 else 1
  return alpha * val + (1 - alpha) * prev


def act(curv_v2, accel):
  return np.array([[curv_v2, accel]], dtype=np.float32)


def test_curvature_is_scaled_by_speed_squared():
  curv, accel, stop = action_from_head(act(4.0, 0.5), 2.0, 0.5, 0.0, smooth, long_smooth_s=0.0, lat_smooth_s=0.0)
  assert curv == pytest.approx(1.0)
  assert accel == pytest.approx(0.5)
  assert not stop


def test_low_speed_floors_the_divisor_at_one():
  # 0.5 m/s is above the lateral cutoff: curvature = 0.5 / max(1, 0.5)^2, unsmoothed with tau 0
  curv, _, _ = action_from_head(act(0.5, 0.0), 0.5, 0.0, 0.0, smooth, lat_smooth_s=0.0)
  assert curv == pytest.approx(0.5)


def test_below_the_lateral_speed_the_previous_curvature_holds():
  curv, _, _ = action_from_head(act(9.0, 0.0), 0.2, 0.0, 0.07, smooth)
  assert curv == pytest.approx(0.07)


def test_acceleration_is_smoothed_over_long_seconds():
  _, accel, _ = action_from_head(act(0.0, 1.0), 10.0, 0.0, 0.0, smooth)
  assert 0.0 < accel < 1.0
  assert accel == pytest.approx(1 - np.exp(-0.05 / 0.3), rel=1e-6)


def test_stop_uses_the_raw_acceleration_before_smoothing():
  assert should_stop(0.1, 0.0)
  assert not should_stop(0.5, 0.0)
  assert not should_stop(0.1, 0.5)
  _, _, stop = action_from_head(act(0.0, 0.0), 0.1, 2.0, 0.0, smooth)
  assert stop
