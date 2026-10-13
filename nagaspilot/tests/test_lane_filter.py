import pytest

from nagaspilot.localization.lane_filter import LaneFilter, Observation

W = 3.5
LINE = W / 2


def obs(lane, n=3, offset=0.0, edges=True, **kw):
  """A car in lane `lane` (0 = leftmost) of an n-lane road, `offset` m right of the lane centre."""
  left_line, right_line = LINE + offset, LINE - offset
  d = dict(left_line_m=left_line, right_line_m=right_line, lane_count=n)
  if edges:
    d.update(left_edge_m=left_line + W * lane + 0.5, right_edge_m=right_line + W * (n - 1 - lane) + 0.5)   # 0.5 m of shoulder
  d.update(kw)
  return Observation(**d)


def test_the_lane_and_offset_come_from_the_lines_and_the_edges():
  f = LaneFilter()
  for lane in (0, 1, 2):
    f = LaneFilter()
    est = None
    for _ in range(6):
      est = f.update(obs(lane))
    assert est.lane_index == lane and est.lane_count == 3 and est.confidence >= 0.7
    assert est.lane_width_m == pytest.approx(W, abs=0.01)
    assert est.offset_m == pytest.approx(0.0, abs=0.01)


def test_offset_is_positive_to_the_right_of_the_lane_centre():
  est = LaneFilter().update(obs(1, offset=0.4))
  assert est.offset_m == pytest.approx(0.4, abs=0.01)
  assert LaneFilter().update(obs(1, offset=-0.4)).offset_m == pytest.approx(-0.4, abs=0.01)


def test_with_no_edges_and_no_prior_the_lane_is_unknown_not_guessed():
  f = LaneFilter()
  est = f.update(obs(1, edges=False))
  assert est.lane_index is None and est.confidence < 0.7
  assert est.offset_m is not None                        # the offset in the lane is still known from the lines


def test_a_completed_lane_change_moves_the_belief_one_lane():
  f = LaneFilter()
  for _ in range(6):
    est = f.update(obs(2))
  assert est.lane_index == 2
  # change to the left (+1), edges now lost (rain): the belief follows the lane change alone
  est = f.update(Observation(left_line_m=LINE, right_line_m=LINE, lane_count=3, lane_change=1))
  assert est.lane_index == 1


def test_a_lane_change_at_the_carriageway_edge_stays_in_range():
  f = LaneFilter()
  for _ in range(6):
    f.update(obs(0))
  est = f.update(Observation(left_line_m=LINE, right_line_m=LINE, lane_count=3, lane_change=1))   # nothing to the left
  assert est.lane_index == 0


def test_a_wrong_belief_recovers_when_the_edges_disagree():
  f = LaneFilter()
  for _ in range(6):
    f.update(obs(0))
  est = None
  for _ in range(15):
    est = f.update(obs(2))
  assert est.lane_index == 2


def test_an_unmapped_road_counts_its_own_lanes_from_the_edges():
  f = LaneFilter()
  est = None
  for _ in range(6):
    est = f.update(obs(1, n=3, lane_count=None))
  assert est.lane_count == 3 and est.lane_index == 1


def test_an_implausible_lane_width_is_not_a_lane():
  f = LaneFilter()
  est = f.update(Observation(left_line_m=0.5, right_line_m=0.5, lane_count=3))     # 1 m: a line pair, not a lane
  assert est.offset_m is None and est.lane_width_m is None and est.lane_index is None
