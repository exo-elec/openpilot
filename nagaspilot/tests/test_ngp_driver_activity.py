import pytest

from nagaspilot.controls.ngp_driver_activity import CRITICAL, OK, PROMPT, SOFT, DriverActivityMonitor

DT = 0.05


def _time_to(stage, v, policy="strict", limit=300.0):
  m = DriverActivityMonitor(policy, DT)
  t = 0.0
  while t < limit:
    t += DT
    if m.update(v, True, False, False).stage == stage:
      return t
  return None


@pytest.mark.parametrize("v,seconds", [(15.0, 60.0), (25.0, 30.0), (35.0, 15.0)])
def test_time_to_critical_follows_the_speed_band(v, seconds):
  assert _time_to(CRITICAL, v) == pytest.approx(seconds, abs=0.2)


@pytest.mark.parametrize("v,seconds", [(15.0, 60.0), (25.0, 30.0), (35.0, 15.0)])
def test_stages_are_proportional_to_the_band_timer(v, seconds):
  assert _time_to(SOFT, v) == pytest.approx(seconds * 0.5, abs=0.2)
  assert _time_to(PROMPT, v) == pytest.approx(seconds * 0.75, abs=0.2)


def test_no_drain_below_the_first_band():
  assert _time_to(SOFT, 8.0, limit=900.0) is None
  assert _time_to(SOFT, 10.4, limit=900.0) is None  # inside the hysteresis margin below 11 m/s


def test_driver_engagement_refills_at_once_in_every_stage():
  for stage_after in (40.0, 55.0, 70.0):
    m = DriverActivityMonitor("strict", DT)
    for _ in range(int(stage_after / DT)):
      m.update(15.0, True, False, False)
    assert m.update(15.0, True, False, True).awareness == 1.0
    assert m.update(15.0, True, False, False).stage == OK


def test_critical_asks_for_deceleration_and_is_never_a_disengage_request():
  m = DriverActivityMonitor("strict", DT)
  status = None
  for _ in range(int(40.0 / DT)):
    status = m.update(25.0, True, False, False)
  assert status.stage == CRITICAL and status.force_decel and status.event == "driverUnresponsive"
  assert status.awareness < 0.0  # awarenessStatus < 0 is what makes controlsd decelerate
  assert not hasattr(status, "disengage")


def test_not_engaged_resets_and_standstill_holds():
  m = DriverActivityMonitor("strict", DT)
  for _ in range(int(20.0 / DT)):
    m.update(25.0, True, False, False)
  held = m.update(0.0, True, True, False).awareness
  assert held == pytest.approx(m.update(0.0, True, True, False).awareness)  # standstill neither drains nor refills
  assert m.update(25.0, False, False, False).awareness == 1.0  # disengaged: fresh start on the next engage


def test_awareness_carries_across_a_band_change():
  m = DriverActivityMonitor("strict", DT)
  for _ in range(int(30.0 / DT)):  # half a minute at 15 m/s: half empty
    m.update(15.0, True, False, False)
  assert m.awareness == pytest.approx(0.5, abs=0.01)
  t = 0.0
  while m.update(28.0, True, False, False).stage != CRITICAL:  # then the 30 s band: 15 s more, not 60
    t += DT
  assert t == pytest.approx(15.0, abs=0.3)


def test_hysteresis_stops_speed_noise_flipping_the_rate_at_an_edge():
  m = DriverActivityMonitor("strict", DT)
  bands = set()
  for i in range(400):
    bands.add(m.update(22.0 + (0.3 if i % 2 else -0.3), True, False, False).band)
  assert len(bands) == 1


def test_unknown_policy_falls_back_to_strict():
  assert _time_to(CRITICAL, 25.0, policy="nope") == pytest.approx(30.0, abs=0.2)


@pytest.mark.parametrize("v,relaxed,tight", [(15.0, 60.0, 30.0), (25.0, 30.0, 15.0), (35.0, 15.0, 10.0)])
def test_relaxed_preserves_deployed_timing_and_tight_warns_sooner(v, relaxed, tight):
  assert _time_to(CRITICAL, v, policy="relaxed") == pytest.approx(relaxed, abs=0.2)
  assert _time_to(CRITICAL, v, policy="tight") == pytest.approx(tight, abs=0.2)


def test_live_policy_change_preserves_critical_awareness():
  m = DriverActivityMonitor("relaxed")
  m.awareness = -0.1
  m.set_policy(b"tight")
  assert m.update(25.0, True, False, False).stage == CRITICAL
  m.set_policy("relaxed")
  assert m.update(25.0, True, False, False).stage == CRITICAL
  assert m.update(25.0, True, False, True).stage == OK


def test_exactly_empty_awareness_matches_legacy_control_deceleration():
  m = DriverActivityMonitor()
  m.awareness = 0.0
  status = m.update(0.0, True, True, False)
  assert status.stage == CRITICAL
  assert status.force_decel == (status.awareness < 0.0)


@pytest.mark.parametrize("edge", [11.0, 22.0, 33.0])
def test_speed_edges_use_hysteresis_without_resetting_decay(edge):
  m = DriverActivityMonitor()
  m.update(edge - 0.6, True, False, False)
  lower_band = m.band
  m.awareness = 0.6
  assert m.update(edge + 0.4, True, False, False).band == lower_band
  assert m.update(edge + 0.5, True, False, False).band == lower_band + 1
  assert m.awareness <= 0.6
  assert m.update(edge - 0.4, True, False, False).band == lower_band + 1
  assert m.update(edge - 0.6, True, False, False).band == lower_band
