"""segd's demand-driven card feed: what each drive context sends to the card."""
from openpilot.selfdrive.segd.schedule import Scheduler, SegContext, grant, wanted_rates

HIGHWAY = 28.0
URBAN = 8.0


def _counts(ctx: SegContext, seconds: int = 10) -> dict[str, int]:
  sched, counts = Scheduler(20.0), {}
  for tick in range(20 * seconds):
    cam = sched.next_camera(tick, ctx)
    if cam is not None:
      counts[cam] = counts.get(cam, 0) + 1
  return {c: n / seconds for c, n in counts.items()}


def _near(actual: dict[str, float], expected: dict[str, float], tol: float = 0.5):
  assert set(actual) == set(expected), (actual, expected)
  for cam, hz in expected.items():
    assert abs(actual[cam] - hz) <= tol, (cam, actual[cam], hz)


def test_idle_highway_feeds_road_and_a_little_wide_only():
  _near(_counts(SegContext(v_ego=HIGHWAY)), {'road': 10, 'wide': 2})


def test_urban_wants_wide_more():
  _near(_counts(SegContext(v_ego=URBAN)), {'road': 10, 'wide': 4})


def test_tele_only_at_speed_and_only_when_fitted():
  _near(_counts(SegContext(v_ego=HIGHWAY, has_tele=True)), {'road': 10, 'wide': 2, 'tele': 4})
  assert 'tele' not in _counts(SegContext(v_ego=URBAN, has_tele=True))
  assert 'tele' not in _counts(SegContext(v_ego=HIGHWAY, has_tele=False))


def test_side_cameras_are_off_until_something_needs_them():
  counts = _counts(SegContext(v_ego=HIGHWAY))
  assert 'side_left' not in counts and 'side_right' not in counts and 'rear' not in counts


def test_left_blinker_feeds_the_left_camera_first_and_never_starves_road():
  counts = _counts(SegContext(v_ego=HIGHWAY, left_blinker=True))
  assert counts['side_left'] >= 5.5 and counts['road'] >= 9.5
  assert 'side_right' not in counts


def test_blindspot_and_side_detection_feed_that_side_only():
  for ctx, side, other in ((SegContext(v_ego=HIGHWAY, right_blindspot=True), 'side_right', 'side_left'),
                           (SegContext(v_ego=HIGHWAY, left_detection=True), 'side_left', 'side_right')):
    counts = _counts(ctx)
    assert 3.5 <= counts[side] <= 4.5 and other not in counts


def test_slow_turn_toward_a_side_feeds_it():
  assert _counts(SegContext(v_ego=4.0, steering_deg=90.0)).get('side_left', 0) >= 5.5
  assert 'side_left' not in _counts(SegContext(v_ego=20.0, steering_deg=90.0))  # fast: not a junction turn


def test_reverse_puts_rear_first_and_drops_wide():
  counts = _counts(SegContext(v_ego=1.0, reverse=True))
  assert counts['rear'] >= 9.5 and counts['road'] <= 2.5 and 'wide' not in counts


def test_rear_detection_feeds_rear_going_forward():
  assert 3.5 <= _counts(SegContext(v_ego=HIGHWAY, rear_detection=True))['rear'] <= 4.5


def test_worst_case_stays_inside_the_budget_and_keeps_road():
  ctx = SegContext(v_ego=URBAN, left_blinker=True, right_blindspot=True, rear_detection=True, has_tele=True)
  counts = _counts(ctx)
  assert sum(counts.values()) <= 20.0 + 0.5
  assert counts['road'] >= 9.5 and counts['side_left'] >= 5.5


def test_grant_follows_priority_when_the_budget_is_short():
  assert grant([('a', 10.0), ('b', 8.0), ('c', 6.0)], 20.0) == {'a': 10.0, 'b': 8.0, 'c': 2.0}
  assert grant([('a', 30.0), ('b', 5.0)], 20.0) == {'a': 20.0}


def test_a_newly_wanted_camera_is_served_at_once():
  sched = Scheduler(20.0)
  quiet = SegContext(v_ego=HIGHWAY)
  for tick in range(40):
    sched.next_camera(tick, quiet)
  blinker = SegContext(v_ego=HIGHWAY, left_blinker=True)
  first_ticks = [sched.next_camera(40 + i, blinker) for i in range(3)]
  assert 'side_left' in first_ticks


def test_a_camera_no_longer_wanted_stops_at_once():
  sched = Scheduler(20.0)
  blinker = SegContext(v_ego=HIGHWAY, left_blinker=True)
  for tick in range(40):
    sched.next_camera(tick, blinker)
  quiet = SegContext(v_ego=HIGHWAY)
  assert all(sched.next_camera(40 + i, quiet) != 'side_left' for i in range(40))


def test_never_more_than_one_job_per_tick_and_road_never_waits_long():
  sched, last_road = Scheduler(20.0), 0
  ctx = SegContext(v_ego=URBAN, left_blinker=True, right_blindspot=True, rear_detection=True, has_tele=True)
  for tick in range(400):
    cam = sched.next_camera(tick, ctx)
    if cam == 'road':
      assert tick - last_road <= 4
      last_road = tick
