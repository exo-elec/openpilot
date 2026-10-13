import json
import math

import pytest

from nagaspilot.localization import lever_arm as L
from nagaspilot.localization import map_horizon as M

LAT0, LON0 = 13.75, 100.5


def point_north(m):
  return (LAT0 + m / L.metres_per_degree(LAT0)[0], LON0)


def line(length=1000.0, step=20.0, start_s=0.0, **kw):
  pts = [point_north(i * step) for i in range(int(length / step) + 1)]
  d = dict(horizon_id=1, route_id='r1', map_version='osm-2026-10', source='phone_osm', start_s=start_s, length_m=length, polyline=pts,
           lanes=[{'s': start_s, 'count': 3, 'recommended': [1, 2]}, {'s': start_s + 600, 'count': 2, 'recommended': [0]}],
           maneuvers=[{'s': start_s + 420, 'type': 'turn_left', 'lane_hint': [0], 'road': 'Rama IV', 'speed_limit_kph': 50}],
           speed_limits=[{'s': start_s, 'kph': 60}, {'s': start_s + 500, 'kph': 50}], ttl_s=30)
  d.update(kw)
  return d


def parse(d, at=100.0):
  return M.MapHorizon.parse(json.dumps(d), at)


def test_a_good_horizon_parses_and_its_arc_length_matches():
  h = parse(line())
  assert h.end_s == pytest.approx(1000.0) and h.arc[-1] == pytest.approx(1000.0, abs=1.0)
  assert h.maneuvers[0].type == 'turn_left' and h.maneuvers[0].lane_hint == (0,)


@pytest.mark.parametrize('bad', [
  dict(length_m=100.0), dict(length_m=9000.0), dict(polyline=[[1, 2]]), dict(polyline=[[95.0, 0.0], [95.0, 0.1]]),
  dict(length_m=700.0),                                                       # does not match the centreline
  dict(maneuvers=[{'s': 5000, 'type': 'x'}]),                                  # outside the window
  dict(horizon_id='x'),
])
def test_bad_horizons_are_refused(bad):
  with pytest.raises(M.BadHorizon):
    parse(line(**bad))
  with pytest.raises(M.BadHorizon):
    M.MapHorizon.parse('not json', 0.0)


def test_a_gap_in_the_centreline_is_refused():
  pts = [point_north(0), point_north(500), point_north(1000)]
  with pytest.raises(M.BadHorizon):
    parse(line(polyline=pts))


def test_locate_gives_arc_length_and_cross_track():
  h = parse(line())
  lat, lon = point_north(420)
  s, xt = h.locate(lat, lon + 5.0 / L.metres_per_degree(LAT0)[1])       # 5 m east of the line
  assert s == pytest.approx(420.0, abs=1.0) and xt == pytest.approx(5.0, abs=0.2)


def test_the_tracker_follows_the_next_maneuver_the_lane_and_the_limit():
  t = M.HorizonTracker()
  assert t.guidance(*point_north(0), 100.0).state == M.NONE
  assert t.offer(parse(line()))
  g = t.guidance(*point_north(100), 101.0)
  assert g.state == M.OK and g.next_maneuver.type == 'turn_left'
  assert g.distance_to_maneuver_m == pytest.approx(320.0, abs=2.0)
  assert g.lane_count == 3 and g.recommended_lanes == (1, 2) and g.speed_limit_kph == 60
  g = t.guidance(*point_north(650), 102.0)
  assert g.next_maneuver is None and g.lane_count == 2 and g.recommended_lanes == (0,) and g.speed_limit_kph == 50


def test_it_asks_for_more_before_the_window_runs_out_but_not_at_the_end_of_the_route():
  t = M.HorizonTracker(min_ahead_m=500.0)
  t.offer(parse(line()))
  assert not t.guidance(*point_north(400), 101.0).wants_refresh        # 600 m left
  g = t.guidance(*point_north(600), 102.0)                              # 400 m left
  assert g.state == M.REFRESH and g.wants_refresh
  t2 = M.HorizonTracker()
  t2.offer(parse(line(end_of_route=True)))
  assert not t2.guidance(*point_north(900), 101.0).wants_refresh        # the destination is in this window


def test_a_newer_horizon_replaces_it_and_an_older_or_repeated_one_does_not():
  t = M.HorizonTracker()
  assert t.offer(parse(line(horizon_id=5)))
  assert not t.offer(parse(line(horizon_id=5)))
  assert not t.offer(parse(line(horizon_id=4)))
  nxt = parse(line(horizon_id=6, start_s=500.0, polyline=[point_north(500 + i * 20.0) for i in range(51)]))
  assert t.offer(nxt)
  g = t.guidance(*point_north(600), 101.0)
  assert g.state == M.OK and g.s == pytest.approx(600.0, abs=1.0)
  assert t.offer(parse(line(horizon_id=1, route_id='other')))            # a new route restarts the ids


def test_off_route_and_stale_read_as_no_guidance_and_ask_for_a_new_window():
  t = M.HorizonTracker()
  t.offer(parse(line(), at=100.0))
  lat, lon = point_north(300)
  far = t.guidance(lat, lon + 80.0 / L.metres_per_degree(LAT0)[1], 101.0)
  assert far.state == M.OFF_ROUTE and far.wants_refresh and far.next_maneuver is None
  assert t.guidance(lat, lon, 100.0 + 31.0).state == M.STALE


def test_the_position_does_not_run_backwards_on_noise_but_a_real_return_is_followed_after_a_reoffer():
  t = M.HorizonTracker()
  t.offer(parse(line()))
  assert t.guidance(*point_north(300), 101.0).s == pytest.approx(300.0, abs=1.0)
  assert t.guidance(*point_north(297), 102.0).s == pytest.approx(300.0, abs=1.0)    # 3 m of noise: held
  assert t.guidance(*point_north(200), 103.0).s == pytest.approx(300.0, abs=1.0)    # 100 m back is not trusted either


def test_a_road_that_loops_back_does_not_snap_to_its_far_side():
  # an out-and-back centreline: north 300 m, then back south alongside (10 m east)
  e = 10.0 / L.metres_per_degree(LAT0)[1]
  up = [point_north(i * 20.0) for i in range(16)]
  down = [(la, LON0 + e) for la, _ in reversed(up)]
  h = parse(line(length=600.0, polyline=up + down, maneuvers=[], lanes=[], speed_limits=[]))
  s, _ = h.locate(*point_north(100), near_s=100.0)
  assert s == pytest.approx(100.0, abs=2.0)
  s2, _ = h.locate(down[3][0], down[3][1], near_s=None)
  assert not math.isclose(s2, 100.0, abs_tol=50.0)


def test_navpilot_fixture_is_accepted_and_followed():
  # written by navpilot's map_horizon_test.dart (MapHorizonBuilder); the two sides must agree on the wire format
  import pathlib
  raw = (pathlib.Path(__file__).parent / 'fixtures' / 'map_horizon_navpilot.json').read_text()
  h = M.MapHorizon.parse(raw, received_at=0.0)
  assert h.map_version is not None and h.source
  assert h.length_m == pytest.approx(1000.0, abs=1.0)
  assert len(h.maneuvers) == 1 and h.maneuvers[0].type == 'turn_left'
  s, cross = h.locate(*h.points[len(h.points) // 2])
  assert abs(cross) < 1.0 and h.start_s < s < h.end_s
