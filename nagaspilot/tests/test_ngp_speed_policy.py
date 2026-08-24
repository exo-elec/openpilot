from nagaspilot.controls.ngp_speed_policy import (
  NGPSpeedPolicy, SpeedLimitObservation, SpeedLimitPolicy, SpeedLimitSource,
)


def nav_obs(limit_mps):
  return (SpeedLimitObservation(source=SpeedLimitSource.NAVIGATION, limit_mps=limit_mps),)


def test_no_observations_suggests_unchanged_v_cruise():
  policy = NGPSpeedPolicy(policy=SpeedLimitPolicy.NAVIGATION)
  result = policy.evaluate(v_ego=25.0, v_cruise=30.0, observations=())
  assert result.source == SpeedLimitSource.NONE
  assert result.resolved_limit_mps is None
  assert result.suggested_cruise_mps == 30.0


def test_nav_limit_below_cruise_clamps_suggestion():
  policy = NGPSpeedPolicy(policy=SpeedLimitPolicy.NAVIGATION)
  result = policy.evaluate(v_ego=25.0, v_cruise=30.0, observations=nav_obs(20.0))
  assert result.source == SpeedLimitSource.NAVIGATION
  assert result.resolved_limit_mps == 20.0
  assert result.suggested_cruise_mps == 20.0


def test_nav_limit_above_cruise_does_not_raise_suggestion():
  """Only ever tightens -- a higher posted limit than v_cruise must not be
  used to accelerate past what the driver already set."""
  policy = NGPSpeedPolicy(policy=SpeedLimitPolicy.NAVIGATION)
  result = policy.evaluate(v_ego=25.0, v_cruise=20.0, observations=nav_obs(30.0))
  assert result.resolved_limit_mps == 30.0
  assert result.suggested_cruise_mps == 20.0


def test_invalid_or_non_positive_observation_is_unusable():
  policy = NGPSpeedPolicy(policy=SpeedLimitPolicy.NAVIGATION)
  invalid = SpeedLimitObservation(source=SpeedLimitSource.NAVIGATION, limit_mps=20.0, valid=False)
  zero = SpeedLimitObservation(source=SpeedLimitSource.NAVIGATION, limit_mps=0.0)
  for obs in (invalid, zero):
    result = policy.evaluate(v_ego=25.0, v_cruise=30.0, observations=(obs,))
    assert result.resolved_limit_mps is None
    assert result.suggested_cruise_mps == 30.0
