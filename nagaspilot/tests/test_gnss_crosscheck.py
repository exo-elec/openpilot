import pytest

from nagaspilot.localization import crosscheck as X
from nagaspilot.localization import lever_arm as L

LAT, LON = 13.75, 100.5


def m_east(m, lat=LAT):
  return m / L.metres_per_degree(lat)[1]


def m_north(m, lat=LAT):
  return m / L.metres_per_degree(lat)[0]


def fix(t=100.0, east=0.0, north=0.0, acc=3.0, speed=0.0, bearing=0.0):
  return X.Fix(t, LAT + m_north(north), LON + m_east(east), acc, speed, bearing)


def test_two_agreeing_fixes_are_combined_and_the_tighter_one_leads():
  comma = fix(acc=4.0, east=2.0)
  phone = fix(acc=1.0, east=0.0)   # phone is on the left of the comma, heading north: its shifted fix is 0.2 m east
  pos = X.combine(comma, phone, 100.0, L.LEFT)
  assert pos.state == X.AGREE and not pos.degraded and pos.source == 'both'
  assert pos.h_acc_m < 1.0                                  # tighter than either
  east_m = (pos.lon - LON) * L.metres_per_degree(LAT)[1]
  assert 0.2 <= east_m < 0.9                                # leaning on the phone (0.2 m), not the comma (2 m)


def test_the_phones_offset_is_removed_before_comparing():
  # a comma at the origin and a phone 0.4 m to its left (west when heading north) read the same car position
  comma = fix(acc=1.0)
  phone = fix(acc=1.0, east=-0.4)
  pos = X.combine(comma, phone, 100.0, L.LEFT, 0.4)
  assert pos.state == X.AGREE and pos.separation_m < 0.05


def test_a_late_phone_fix_is_carried_to_now_by_its_own_motion():
  phone = fix(t=99.0, north=0.0, speed=20.0, bearing=0.0)     # a second old, moving north at 20 m/s
  comma = fix(t=100.0, north=20.0, acc=3.0)                   # the comma's fix is current and 20 m further north
  pos = X.combine(comma, phone, 100.0, L.LEFT, 0.0)
  assert pos.state == X.AGREE and pos.separation_m < 0.5


def test_fixes_that_differ_by_more_than_they_claim_disagree_and_the_phone_wins_with_no_prior():
  comma = fix(acc=3.0, north=40.0)
  phone = fix(acc=1.0)
  pos = X.combine(comma, phone, 100.0, L.LEFT, 0.0)
  assert pos.state == X.DISAGREE_PHONE and pos.degraded and pos.source == 'phone'
  assert pos.separation_m == pytest.approx(40.0, abs=0.1)


def test_the_previous_position_decides_when_the_phone_is_the_one_that_jumped():
  prior = X.Position(X.AGREE, LAT, LON, 1.0, None, False, 'both')
  comma = fix(acc=3.0)
  phone = fix(acc=1.0, north=60.0)                             # the phone jumps 60 m, the comma stays with the prior
  pos = X.combine(comma, phone, 100.0, L.LEFT, 0.0, prior)
  assert pos.state == X.DISAGREE_COMMA and pos.source == 'comma'


def test_one_fresh_fix_is_used_and_marked_degraded_and_stale_ones_are_ignored():
  phone = fix(acc=1.0)
  assert X.combine(None, phone, 100.0, L.LEFT).state == X.PHONE_ONLY
  comma = fix(acc=3.0)
  assert X.combine(comma, None, 100.0, L.LEFT).state == X.COMMA_ONLY
  assert X.combine(comma, fix(t=90.0), 100.0, L.LEFT).state == X.COMMA_ONLY      # the phone's is 10 s old
  assert X.combine(fix(t=90.0), fix(t=90.0), 100.0, L.LEFT).state == X.NONE
  assert X.combine(None, None, 100.0, L.LEFT).state == X.NONE
  assert X.combine(comma, phone, 100.0, L.LEFT).degraded is False


def test_a_claimed_accuracy_better_than_the_floor_is_not_believed():
  comma = fix(acc=0.0)
  phone = fix(acc=0.0, east=0.3)
  pos = X.combine(comma, phone, 100.0, L.RIGHT, 0.0)
  assert pos.h_acc_m >= X.MIN_SIGMA_M / 2 ** 0.5 - 1e-9


def test_bad_speed_is_not_used_to_carry_a_fix():
  f = fix(t=99.0, speed=500.0, bearing=90.0)
  assert X.advance(f, 100.0) == f
