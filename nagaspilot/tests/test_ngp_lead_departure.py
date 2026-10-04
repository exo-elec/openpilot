from types import SimpleNamespace as NS

from nagaspilot.controls.ngp_lead_departure import NGPLeadDeparture


def lead(d, v=0.0, status=True, track=3):
  return NS(status=status, dRel=d, vLead=v, radarTrackId=track)


def test_reports_departure_only_after_range_and_speed_grow():
  ld = NGPLeadDeparture()
  assert not ld.update(True, lead(8.0))  # baseline
  assert not ld.update(True, lead(8.5, v=0.5))
  assert not ld.update(True, lead(9.5, v=0.5))  # far enough, too slow
  assert ld.update(True, lead(9.5, v=2.0))


def test_not_stopped_or_no_lead_resets():
  ld = NGPLeadDeparture()
  ld.update(True, lead(8.0))
  assert not ld.update(False, lead(12.0, v=3.0))
  assert not ld.update(True, lead(12.0, v=3.0))  # new baseline after the reset
  ld.update(True, lead(8.0))
  assert not ld.update(True, lead(20.0, v=3.0, status=False))


def test_creeping_closer_then_leaving_counts_from_the_closest_range():
  ld = NGPLeadDeparture()
  ld.update(True, lead(8.0))
  ld.update(True, lead(6.0))
  assert ld.update(True, lead(7.5, v=2.0))


def test_lead_swap_is_not_a_departure():
  ld = NGPLeadDeparture()
  ld.update(True, lead(8.0, track=3))
  assert not ld.update(True, lead(15.0, v=3.0, track=9))
