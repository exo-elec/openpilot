import json

import pytest

from nagaspilot.localization import crosscheck as X
from nagaspilot.localization import phone_fix as P


def msg(**kw):
  d = dict(t_ms=1_760_000_000_000, lat=13.7563, lon=100.5018, h_acc=1.2, speed=18.5, bearing=92.0, mount={'side': 'right', 'offset_m': 0.3})
  d.update(kw)
  return json.dumps(d)


def test_a_good_message_becomes_a_fix_on_the_comma_clock_with_its_mount():
  clock = P.ClockOffset()
  fix, mount = P.parse(msg(), arrival_s=5000.25, clock=clock)
  assert isinstance(fix, X.Fix)
  assert (fix.lat, fix.lon, fix.h_acc_m, fix.speed_mps, fix.bearing_deg) == (13.7563, 100.5018, 1.2, 18.5, 92.0)
  assert mount == P.Mount('right', 0.3)
  assert fix.t == pytest.approx(5000.25)             # the only sample: offset = arrival - stamp, so the fix lands on its arrival


def test_the_offset_is_the_least_delayed_message_so_a_slow_one_does_not_move_it():
  clock = P.ClockOffset()
  base = 1_760_000_000.0
  for i, delay in enumerate((0.20, 0.05, 0.40, 0.08)):
    fix, _ = P.parse(msg(t_ms=int((base + i) * 1000)), arrival_s=3000.0 + i + delay, clock=clock)
  # offset = 3000 - base + 0.05 (the 0.05 s message); the last fix (delay 0.08) lands 0.03 s after its true time
  assert clock.offset == pytest.approx(3000.0 - base + 0.05)
  assert fix.t == pytest.approx(3000.0 + 3 + 0.05)


def test_a_phone_clock_that_is_wrong_by_an_hour_still_lines_up():
  clock = P.ClockOffset()
  fix, _ = P.parse(msg(t_ms=1_760_003_600_000), arrival_s=100.0, clock=clock)    # the phone is an hour ahead of the comma's clock
  assert fix.t == pytest.approx(100.0)


@pytest.mark.parametrize('bad', [
  'not json', json.dumps({}), msg(lat=95.0), msg(lon=-181.0), msg(h_acc=-1.0), msg(h_acc=900.0), msg(speed=400.0), msg(t_ms='x'),
  msg(mount={'side': 'centre', 'offset_m': 0.2}), msg(mount={'side': 'left', 'offset_m': 5.0}),
])
def test_bad_messages_are_refused_and_do_not_disturb_the_clock(bad):
  clock = P.ClockOffset()
  with pytest.raises(P.BadFix):
    P.parse(bad, arrival_s=10.0, clock=clock)
  assert clock.offset is None


def test_a_missing_optional_field_defaults_and_the_fix_feeds_the_cross_check():
  clock = P.ClockOffset()
  fix, mount = P.parse(json.dumps({'t_ms': 1000, 'lat': 13.75, 'lon': 100.5, 'h_acc': 1.0}), 50.0, clock)
  assert (fix.speed_mps, fix.bearing_deg) == (0.0, 0.0) and mount == P.Mount('left', 0.2)
  comma = X.Fix(50.0, 13.75, 100.5, 3.0)
  pos = X.combine(comma, fix, 50.0, mount.side, mount.offset_m)
  assert pos.state == X.AGREE
