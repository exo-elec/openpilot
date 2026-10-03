from types import SimpleNamespace as NS

from cereal import log
from nagaspilot.controls.eop_lane_change import (
  blindspot_blocked, evaluate_gap, is_road_edge_blinker, validate_lane_width,
)

LCD = log.LaneChangeDirection


def _line(y, n=8):
  return NS(x=[5.0 * (i + 1) for i in range(n)], y=[y] * n)


def _model(lead=None, probs=(0.9, 0.9, 0.9, 0.9), stds=(1.0, 1.0)):
  return NS(laneLines=[_line(-5.2), _line(-1.8), _line(1.8), _line(5.2)], roadEdges=[_line(-7.0), _line(7.0)],
            roadEdgeStds=list(stds), laneLineProbs=list(probs), leadsV3=[lead] if lead else [])


def test_road_edge_blocks_only_blinker_toward_edge():
  # strong right road edge, no right lane line, left lane line present
  md = _model(probs=(0.9, 0.9, 0.9, 0.05), stds=(1.0, 0.1))
  assert is_road_edge_blinker(md, right_blinker=True, left_blinker=False)
  assert not is_road_edge_blinker(md, right_blinker=False, left_blinker=True)
  assert not is_road_edge_blinker(None, True, False)


def test_gap_rejects_fast_approach_and_accepts_empty_lane():
  assert evaluate_gap(None, _model(), 'left', 20.0) == (True, 1.0)
  # vision lead in the left lane (y negative = left in model frame), closing at 15 m/s
  closing = NS(prob=0.9, x=[20.0], y=[-3.0], v=[5.0])
  assert evaluate_gap(None, _model(closing), 'left', 20.0) == (False, 0.0)
  assert evaluate_gap(None, _model(closing), 'right', 20.0) == (True, 1.0)


def test_lane_width_rejects_shoulder_and_accepts_normal_lane():
  assert validate_lane_width(_model(), 'left', 2.5)
  narrow = _model()
  narrow.roadEdges = [_line(-2.4), _line(2.4)]  # edge only 0.6 m outside the inner line
  assert not validate_lane_width(narrow, 'left', 2.5)
  assert validate_lane_width(None, 'left', 2.5)


def test_blindspot_priority_vehicle_bsm_then_zones():
  cs = NS(leftBlindspot=False, rightBlindspot=True)
  assert blindspot_blocked(cs, None, LCD.right)
  assert not blindspot_blocked(cs, None, LCD.left)
  bsa = NS(leftDetected=False, rightDetected=False, leftAlertLevel=0, rightAlertLevel=0, lcaBlockedLeft=True, lcaBlockedRight=False)
  assert blindspot_blocked(NS(leftBlindspot=False, rightBlindspot=False), bsa, LCD.left)
  assert not blindspot_blocked(NS(leftBlindspot=False, rightBlindspot=False), bsa, LCD.right)
