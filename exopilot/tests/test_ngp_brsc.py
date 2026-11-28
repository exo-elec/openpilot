from exopilot.selfdrive.controls.lib.brsc import BRSC as NGPBRSC

DT = 1.0 / 20.0  # planner rate
GRAVITY = 9.81


def feed(ctrl, az_values, dt=DT, accel_max_full=2.0):
  result = None
  for az in az_values:
    result = ctrl.update(az, dt, accel_max_full=accel_max_full)
  return result


def test_smooth_road_stays_inactive():
  ctrl = NGPBRSC()
  result = feed(ctrl, [GRAVITY] * 100)
  assert not result.active
  assert result.speed_factor == 1.0
  assert result.roughness_rms < NGPBRSC.RMS_MILD


def test_single_expansion_joint_does_not_trigger():
  """A single-frame spike (< ATTACK_S) must not engage a slowdown."""
  ctrl = NGPBRSC()
  feed(ctrl, [GRAVITY] * 40)
  result = ctrl.update(GRAVITY + 6.0, DT)  # one spike, one frame
  result = feed(ctrl, [GRAVITY] * 20)
  assert not result.active
  assert result.speed_factor == 1.0


def test_sustained_roughness_engages_and_caps_reduction():
  ctrl = NGPBRSC()
  feed(ctrl, [GRAVITY] * 20)
  import math
  rough = [GRAVITY + 4.0 * math.sin(i) for i in range(200)]
  result = feed(ctrl, rough)
  assert result.active
  assert result.speed_factor < 1.0
  assert result.speed_factor >= NGPBRSC.SPEED_FACTOR_FLOOR
  assert result.accel_max < 2.0


def test_reduction_never_exceeds_floor_even_for_extreme_roughness():
  ctrl = NGPBRSC()
  feed(ctrl, [GRAVITY] * 20)
  import math
  extreme = [GRAVITY + 20.0 * math.sin(i) for i in range(200)]
  result = feed(ctrl, extreme)
  assert result.speed_factor == NGPBRSC.SPEED_FACTOR_FLOOR
  assert result.accel_max >= 2.0 * NGPBRSC.ACCEL_MAX_FLOOR_FRACTION - 1e-6


def test_hold_keeps_engaged_through_short_pause_between_bumps():
  """Two close-together bump events (e.g. a rail crossing's two rails) should not
  cause the controller to fully release in between."""
  ctrl = NGPBRSC()
  import math
  feed(ctrl, [GRAVITY] * 20)
  feed(ctrl, [GRAVITY + 5.0 * math.sin(i) for i in range(20)])  # first bump, engages
  mid = feed(ctrl, [GRAVITY] * 10)  # brief smooth gap, well under hold time
  assert mid.active
  after = feed(ctrl, [GRAVITY + 5.0 * math.sin(i) for i in range(20)])  # second bump
  assert after.active
  assert after.hold_remaining > 0.0


def test_release_ramps_back_after_hold_expires_not_a_step():
  ctrl = NGPBRSC()
  import math
  feed(ctrl, [GRAVITY] * 20)
  feed(ctrl, [GRAVITY + 4.0 * math.sin(i) for i in range(200)])
  # Long smooth stretch: hold should expire, then speed_factor should ramp, not snap.
  factors = []
  for _ in range(400):
    r = ctrl.update(GRAVITY, DT)
    factors.append(r.speed_factor)
  assert factors[0] < 1.0  # still recovering just after roughness stops
  assert all(b >= a - 1e-9 for a, b in zip(factors, factors[1:], strict=False))  # monotonically non-decreasing
  assert factors[-1] == 1.0  # fully recovered eventually
  assert not feed(ctrl, [GRAVITY] * 5).active


def test_hold_time_is_bounded_by_cap():
  ctrl = NGPBRSC()
  import math
  feed(ctrl, [GRAVITY] * 20)
  for _ in range(30):
    feed(ctrl, [GRAVITY + 5.0 * math.sin(i) for i in range(10)])
    result = feed(ctrl, [GRAVITY] * 2)
  assert result.hold_remaining <= NGPBRSC.HOLD_CAP_S


def test_non_finite_inputs_and_negative_accel_limit_do_not_poison_state():
  import math
  ctrl = NGPBRSC()
  feed(ctrl, [GRAVITY] * 20)
  r_nan = ctrl.update(float('nan'), DT, accel_max_full=2.0)
  assert math.isfinite(r_nan.roughness_rms)
  assert math.isfinite(r_nan.speed_factor)
  r_dt_nan = ctrl.update(GRAVITY, float('nan'), accel_max_full=2.0)
  assert math.isfinite(r_dt_nan.roughness_rms)
  assert len(ctrl._window) <= 20
  # Negative accel_max_full must never be raised (less negative) by BRSC.
  feed(ctrl, [GRAVITY + 5.0 * math.sin(i) for i in range(40)])
  r_neg = ctrl.update(GRAVITY + 5.0, DT, accel_max_full=-1.5)
  assert r_neg.accel_max == -1.5
  ctrl.reset()
  r_after_reset = ctrl.update(GRAVITY, DT, accel_max_full=2.0)
  assert not r_after_reset.active
  assert r_after_reset.speed_factor == 1.0
