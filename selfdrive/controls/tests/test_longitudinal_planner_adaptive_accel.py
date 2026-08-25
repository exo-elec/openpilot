from openpilot.selfdrive.controls.lib.longitudinal_planner import (
  ADAPTIVE_ACCEL_CITY_SPEED_LIMIT, _apply_adaptive_accel_limit,
)

RAW_MAX_ACCEL = 2.0


def test_standstill_clamps_to_quarter_max():
  # Far below v_cruise so the ramp-off term doesn't bind.
  result = _apply_adaptive_accel_limit(RAW_MAX_ACCEL, v_cruise=30.0, v_ego=0.0)
  assert result == RAW_MAX_ACCEL / 4


def test_half_city_speed_clamps_to_half_max():
  result = _apply_adaptive_accel_limit(RAW_MAX_ACCEL, v_cruise=30.0, v_ego=ADAPTIVE_ACCEL_CITY_SPEED_LIMIT / 2)
  assert result == RAW_MAX_ACCEL / 2


def test_at_or_above_city_speed_low_speed_clamp_is_full_max():
  # Still far below v_cruise, so only the low-speed term is being tested.
  result = _apply_adaptive_accel_limit(RAW_MAX_ACCEL, v_cruise=30.0, v_ego=ADAPTIVE_ACCEL_CITY_SPEED_LIMIT)
  assert result == RAW_MAX_ACCEL


def test_ramps_off_to_zero_at_cruise_setpoint():
  result = _apply_adaptive_accel_limit(RAW_MAX_ACCEL, v_cruise=20.0, v_ego=20.0)
  assert result == 0.0


def test_ramps_off_partially_within_one_to_five_mps_of_setpoint():
  # The ramp_off interp's middle breakpoint (x=1.0 -> y=0.5) is a literal
  # 0.5 m/s^2, not raw_max_accel/2 -- verified against the source formula.
  result = _apply_adaptive_accel_limit(RAW_MAX_ACCEL, v_cruise=20.0, v_ego=19.0)
  assert result == 0.5


def test_low_speed_and_near_setpoint_together_takes_the_tighter_term():
  # Creep-in-traffic case: low v_ego AND close to v_cruise, so both interp
  # terms are in-range and could plausibly bind. low_speed_limit =
  # interp(3.0, [0, 6.95, 13.9], [0.5, 1.0, 2.0]) ~= 0.716; ramp_off =
  # interp(0.5, [0, 1, 5], [0, 0.5, 2.0]) = 0.25. min() must pick ramp_off.
  result = _apply_adaptive_accel_limit(RAW_MAX_ACCEL, v_cruise=3.5, v_ego=3.0)
  assert result == 0.25


def test_never_exceeds_raw_max_accel():
  # Far from setpoint and well above city speed -- both interp terms exceed
  # raw_max_accel, but the result must not.
  result = _apply_adaptive_accel_limit(RAW_MAX_ACCEL, v_cruise=100.0, v_ego=40.0)
  assert result == RAW_MAX_ACCEL
