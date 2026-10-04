"""EOP10's adaptd computer, ported: reproduces the original's recorded outputs; the sentinel fix; the consumers' clamp."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

from nagaspilot.controls.ngp_adaptive_driving import AdaptiveDrivingComputer
from nagaspilot.controls.ngp_adaptive_limits import clamp_accel_limits, personality_from_state

G = json.loads((Path(__file__).parent / 'eop_golden_adapt.json').read_text())


def vd(**kw):
  base = dict(valid=True, batterySoc=-1.0, rangeRemaining=-1.0, batteryTempMax=-273.0, batteryTempMin=-273.0, motorTemp=-273.0, inverterTemp=-273.0, coolantTemp=-273.0)
  base.update(kw)
  return NS(**base)


class Clock:
  t = 1000.0

  def __call__(self):
    return self.t


def test_reproduces_the_original_including_its_cold_sentinel_behaviour():
  clk = Clock()
  c = AdaptiveDrivingComputer(clock=clk, legacy_cold_sentinel_bug=True)
  c.set_enabled(True)
  for row in G['cases']:
    clk.t += 11.0
    p = c.update(vd(**row['in']))
    assert (int(p.personality), p.accel_max, p.accel_min, p.regen_strength, p.thermal_derating, p.reason, p.reason_code, p.enabled) == \
           (row['personality'], row['accel_max'], row['accel_min'], row['regen'], row['derating'], row['reason'], row['code'], row['enabled'])


def test_hysteresis_reproduces_the_original():
  clk = Clock()
  c = AdaptiveDrivingComputer(clock=clk, legacy_cold_sentinel_bug=True)
  c.set_enabled(True)
  for row in G['hysteresis']:
    clk.t += row['dt']
    assert int(c.update(vd(batterySoc=row['soc'])).personality) == row['personality']


def test_the_fix_ignores_the_unknown_temperature_sentinel_but_not_a_real_cold_battery():
  clk = Clock()
  c = AdaptiveDrivingComputer(clock=clk)
  c.set_enabled(True)
  clk.t += 11.0
  p = c.update(vd(batterySoc=50.0))
  assert p.reason_code == 'normal' and p.regen_strength == 1.0                      # no battery temperature known: nothing to limit
  clk.t += 11.0
  assert c.update(vd(batterySoc=50.0, batteryTempMin=-5.0)).reason_code == 'cold_batt_temp'
  clk.t += 11.0
  assert c.update(vd(batteryTempMax=60.0)).thermal_derating is True


def test_consumers_only_tighten_and_cap_the_personality():
  assert clamp_accel_limits((-3.48, 2.0), True, 1.2, 2.0) == (-2.0, 1.2)
  assert clamp_accel_limits((-3.48, 2.0), True, 5.0, 5.0) == (-3.48, 2.0)             # looser than the controller's own: unchanged
  assert clamp_accel_limits((-3.48, 2.0), False, 1.0, 1.0) == (-3.48, 2.0)
  assert clamp_accel_limits((-3.48, 2.0), True, 0.0, 1.0) == (-3.48, 2.0)             # 0 = no limit
  assert clamp_accel_limits((-3.48, 2.0), True, float('nan'), 1.0) == (-3.48, 2.0)
  assert personality_from_state(1, True, 3, max_personality=2) == 2 and personality_from_state(1, True, 3) == 3
  assert personality_from_state(1, False, 3) == 1 and personality_from_state(1, True, 9) == 1
