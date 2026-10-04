from nagaspilot.controls.ngp_blinker_pause import NGPBlinkerPause
from nagaspilot.controls.ngp_green_light import NGPGreenLight

MIN = 9.0  # m/s


def test_pauses_only_below_speed_with_one_blinker():
  p = NGPBlinkerPause(MIN)
  assert p.update(5.0, True, False) and p.update(5.0, False, True)
  assert not NGPBlinkerPause(MIN).update(5.0, False, False)
  assert not NGPBlinkerPause(MIN).update(5.0, True, True)  # hazards are not a turn
  assert not NGPBlinkerPause(MIN).update(15.0, True, False)  # lane change speed: lateral stays on


def test_holds_after_the_blinker_goes_off_then_releases():
  p = NGPBlinkerPause(MIN, hold_s=1.5, dt=0.01)
  p.update(5.0, True, False)
  frames = 0
  while p.update(5.0, False, False):
    frames += 1
    assert frames < 1000
  assert 145 <= frames <= 151  # ~1.5 s at 100 Hz


def test_hold_is_dropped_when_speed_picks_up_and_disabled_when_zero():
  p = NGPBlinkerPause(MIN)
  p.update(5.0, True, False)
  assert not p.update(20.0, False, False)
  assert not p.update(5.0, False, False)  # no leftover hold after leaving the low-speed regime
  off = NGPBlinkerPause(0.0)
  assert not off.update(1.0, True, False)


def test_green_light_fires_once_when_released_while_stopped():
  g = NGPGreenLight()
  assert not g.update(False, True)
  assert not g.update(True, True)
  assert g.update(False, True)
  assert not g.update(False, True)  # only on the release tick


def test_green_light_needs_standstill_and_a_prior_force_stop():
  g = NGPGreenLight()
  g.update(True, False)
  assert not g.update(False, False)  # already rolling
  g2 = NGPGreenLight()
  assert not g2.update(False, True)  # was never held
