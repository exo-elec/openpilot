from types import SimpleNamespace

from nagaspilot.controls.ngp_vtsc import NGPVTSC, VTSCState

V_EGO = 25.0  # m/s, well above MIN_VELOCITY


def model(rate_z, v_x=None, n=10):
  velocities = v_x if v_x is not None else [V_EGO] * n
  return SimpleNamespace(
    orientationRate=SimpleNamespace(z=[rate_z] * n),
    velocity=SimpleNamespace(x=velocities),
  )


def feed(ctrl, rate_z, v_ego=V_EGO, n=30, enabled=True):
  result = None
  for _ in range(n):
    result = ctrl.update(v_ego, model(rate_z), enabled=enabled)
  return result


def test_straight_road_stays_enabled_with_no_target():
  ctrl = NGPVTSC()
  result = feed(ctrl, rate_z=0.0)
  assert result.state == VTSCState.ENABLED
  assert result.target_speed is None


def test_sustained_curve_enters_turning_and_produces_target():
  ctrl = NGPVTSC()
  # A steady high yaw rate at speed keeps predicted lat accel above the
  # entering/turning thresholds. One state transition per update() call:
  # call 1 DISABLED->ENABLED, call 2 ENABLED->ENTERING, call 3
  # ENTERING->TURNING (current=2.5 >= TURNING_LAT_ACC=1.6), then holds
  # TURNING for the remaining calls (current never drops to <= 1.3).
  result = feed(ctrl, rate_z=0.1, n=30)  # |0.1| * 25 m/s = 2.5 m/s^2 predicted
  assert result.state == VTSCState.TURNING
  assert result.target_speed is not None
  assert result.target_speed > 0.0


def test_disabled_forces_disabled_state_and_no_target():
  ctrl = NGPVTSC()
  result = feed(ctrl, rate_z=0.1, enabled=False)
  assert result.state == VTSCState.DISABLED
  assert result.target_speed is None


def test_below_min_velocity_forces_disabled_state():
  ctrl = NGPVTSC()
  result = feed(ctrl, rate_z=0.1, v_ego=NGPVTSC.MIN_VELOCITY - 1.0)
  assert result.state == VTSCState.DISABLED
  assert result.target_speed is None


def test_leaving_curve_returns_toward_enabled():
  ctrl = NGPVTSC()
  feed(ctrl, rate_z=0.1, n=30)  # get into TURNING (see test above)
  # curve ends: call 1 TURNING->LEAVING (current=0 <= LEAVING_LAT_ACC=1.3),
  # call 2 LEAVING->ENABLED (current=0 < ENABLED_LAT_ACC=1.1), then holds
  # ENABLED for the remaining calls.
  result = feed(ctrl, rate_z=0.0, n=5)
  assert result.state == VTSCState.ENABLED
  assert result.target_speed is None
