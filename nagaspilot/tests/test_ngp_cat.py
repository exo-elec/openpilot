from types import SimpleNamespace as NS

from nagaspilot.controls.ngp_cat import MIN_SAMPLES, NGPCAT, live_params_gated

BASE_SR = 15.0


def _lp(**kw):
  d = dict(valid=True, posenetValid=True, sensorValid=True, steerRatioValid=True, stiffnessFactorValid=True)
  d.update(kw)
  return NS(**d)


def _cs(v=20.0, angle=5.0):
  return NS(vEgo=v, steeringAngleDeg=angle)


def test_gate_requires_valid_learner_speed_and_small_angle():
  assert live_params_gated(_lp(), _cs())
  assert not live_params_gated(_lp(valid=False), _cs())
  assert not live_params_gated(_lp(steerRatioValid=False), _cs())
  assert not live_params_gated(_lp(), _cs(v=3.0))
  assert not live_params_gated(_lp(), _cs(angle=60.0))


def test_becomes_adaptive_only_after_enough_valid_ticks_and_follows_the_learner():
  cat = NGPCAT(BASE_SR)
  t = 0.0
  for i in range(MIN_SAMPLES - 1):
    t += 0.05
    status = cat.step(t, True, 17.0, 1.1, 0.5)
    assert not status.adaptive, i
  t += 0.05
  status = cat.step(t, True, 17.0, 1.1, 0.5)
  assert status.adaptive and status.samples >= MIN_SAMPLES
  assert BASE_SR < status.steer_ratio < 17.0  # smoothed, not jumped
  params = cat.adaptive_params()
  assert params["steerRatio"] == status.steer_ratio


def test_not_adaptive_returns_base_values_and_confidence_decays_when_gated_out():
  cat = NGPCAT(BASE_SR)
  t = 0.0
  for _ in range(MIN_SAMPLES + 5):
    t += 0.05
    cat.step(t, True, 16.0, 1.0, 0.0)
  assert cat.status.adaptive
  for _ in range(400):  # 20 s of gated-out ticks
    t += 0.05
    cat.step(t, False, 99.0, 9.0, 9.0)
  assert not cat.status.adaptive
  assert cat.adaptive_params() == {"steerRatio": BASE_SR, "stiffnessFactor": 1.0, "angleOffsetDeg": 0.0}


def test_learner_steer_ratio_is_clamped_to_half_and_double_the_base():
  cat = NGPCAT(BASE_SR)
  t = 0.0
  for _ in range(MIN_SAMPLES * 40):
    t += 0.05
    cat.step(t, True, 1000.0, 1.0, 0.0)
  assert cat.status.steer_ratio <= 2.0 * BASE_SR + 1e-6


def test_manual_steer_ratio_pins_the_value():
  cat = NGPCAT(BASE_SR)
  status = cat.step(0.05, True, 17.0, 1.0, 0.0, manual_sr=14.0)
  assert status.adaptive and status.steer_ratio == 14.0 and status.note == "manual_sr"
