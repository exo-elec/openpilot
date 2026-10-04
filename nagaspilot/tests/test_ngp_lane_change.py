from types import SimpleNamespace as NS

from nagaspilot.controls.ngp_lane_change import evaluate_gap, validate_lane_width


def _line(y, n=8):
  return NS(x=[5.0 * (i + 1) for i in range(n)], y=[y] * n)


def _model(leads=()):
  return NS(laneLines=[_line(-5.2), _line(-1.8), _line(1.8), _line(5.2)], roadEdges=[_line(-7.0), _line(7.0)], leadsV3=list(leads))


def _lead(x, y, v, prob=0.9):
  return NS(prob=prob, x=[x], y=[y], v=[v])


def test_empty_or_unknown_data_never_blocks():
  assert evaluate_gap(_model(), 'left', 20.0) == (True, 1.0)
  assert evaluate_gap(None, 'left', 20.0) == (True, 1.0)
  assert validate_lane_width(None, 'left')
  assert validate_lane_width(NS(laneLines=[], roadEdges=[]), 'left')


def test_vision_lead_side_uses_right_positive_model_frame():
  closing = _lead(20.0, -3.0, 5.0)  # model y negative = left of the car
  assert evaluate_gap(_model([closing]), 'left', 20.0) == (False, 0.0)
  assert evaluate_gap(_model([closing]), 'right', 20.0) == (True, 1.0)
  assert evaluate_gap(_model([_lead(20.0, 3.0, 5.0)]), 'right', 20.0) == (False, 0.0)


def test_ttc_and_low_confidence_leads():
  assert evaluate_gap(_model([_lead(40.0, -3.0, 15.0)]), 'left', 20.0)[0]  # TTC ~8 s
  assert not evaluate_gap(_model([_lead(12.0, -3.0, 14.0)]), 'left', 20.0)[0] 
  assert evaluate_gap(_model([_lead(20.0, -3.0, 5.0, prob=0.2)]), 'left', 20.0)[0]
  assert evaluate_gap(_model([_lead(8.0, -3.0, 20.0)]), 'left', 20.0) == (False, 0.3)  # level and close


def test_in_lane_lead_is_ignored():
  assert evaluate_gap(_model([_lead(15.0, -0.2, 0.0)]), 'left', 20.0) == (True, 1.0)


def test_radar_leads_follow_left_positive_yrel():
  lead = NS(status=True, dRel=15.0, yRel=3.0, vRel=-12.0)
  rs = NS(leadOne=lead, leadTwo=NS(status=False))
  assert evaluate_gap(None, 'left', 20.0, radar_state=rs) == (False, 0.0)
  assert evaluate_gap(None, 'right', 20.0, radar_state=rs) == (True, 1.0)


def test_lane_width_rejects_shoulder_only():
  assert validate_lane_width(_model(), 'left') and validate_lane_width(_model(), 'right')
  narrow = _model()
  narrow.roadEdges = [_line(-2.4), _line(2.4)]
  assert not validate_lane_width(narrow, 'left')
  assert not validate_lane_width(narrow, 'right')
