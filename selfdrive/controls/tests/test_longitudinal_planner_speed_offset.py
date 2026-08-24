from openpilot.common.constants import CV
from openpilot.selfdrive.controls.lib.longitudinal_planner import _apply_speed_offset


def test_zero_offset_is_a_no_op():
  v_cruise = 25.0
  assert _apply_speed_offset(v_cruise, 0.0) == v_cruise


def test_positive_offset_adds_kph_converted_to_ms():
  v_cruise_kph = 100.0
  v_cruise = v_cruise_kph * CV.KPH_TO_MS
  result = _apply_speed_offset(v_cruise, 5.0)
  assert result == (v_cruise_kph + 5.0) * CV.KPH_TO_MS


def test_negative_offset_subtracts():
  v_cruise_kph = 100.0
  v_cruise = v_cruise_kph * CV.KPH_TO_MS
  result = _apply_speed_offset(v_cruise, -10.0)
  assert result == (v_cruise_kph - 10.0) * CV.KPH_TO_MS


def test_offset_can_push_v_cruise_above_a_prior_zero():
  """_apply_speed_offset itself is unconditional -- it has no notion of
  force_slow_decel. This is EOP10's own behavior for this function. The
  guard against overriding a forced deceleration lives at the call site in
  longitudinal_planner.py's update() (`if self.speed_offset_kph and not
  force_slow_decel`), a single boolean condition visible directly at the
  call site -- not covered by a dedicated test here since exercising it
  requires a full update() call with a mocked SubMaster/mpc, which this
  test file's pure-function-only scope deliberately avoids. This test only
  documents the pure function's math."""
  result = _apply_speed_offset(0.0, 5.0)
  assert result == 5.0 * CV.KPH_TO_MS


def test_large_negative_offset_is_not_bounded_and_can_go_negative():
  """Bounded-output gap, not a regression: _apply_speed_offset has no floor
  or ceiling on either the input offset or the result, matching EOP10's own
  driver_prefs.py::get_speed_with_offset() (also unconditional addition, no
  clamp). ngp_lon_speed_offset_kph has no panel toggle on either branch, so
  nothing in the UI constrains what value gets written to the param -- only
  a direct param write (SSH/API) can set it, but nothing downstream of
  _apply_speed_offset clamps the result before it reaches the MPC either.
  This test documents that an extreme, plausible-looking misconfiguration
  (a driver or tool typing -200 intending a modest offset) drives v_cruise
  negative, not that this code path guards against it -- it doesn't."""
  v_cruise_kph = 30.0
  v_cruise = v_cruise_kph * CV.KPH_TO_MS
  result = _apply_speed_offset(v_cruise, -200.0)
  assert result < 0.0
