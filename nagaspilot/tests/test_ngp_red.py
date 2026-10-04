from types import SimpleNamespace as NS

from nagaspilot.controls.ngp_red import NGPRED, REDState, RoadEdge, RoadEdgeType, curvature_nudge, vision_edges


def _edge(y, n=10):
  return NS(x=[float(5 * i) for i in range(1, n + 1)], y=[y] * n)


def _model(left_y=-1.8, right_y=1.8, left_std=0.05, right_std=0.05):
  return NS(roadEdges=[_edge(left_y), _edge(right_y)], roadEdgeStds=[left_std, right_std])


def _run(red, model, frames=5, laneless=True, v=20.0):
  out = None
  for _ in range(frames):
    out = red.update(model, (0.0, 0.0), v, [], laneless)
  return out


def test_confidence_comes_from_the_road_edge_std_and_weak_edges_are_ignored():
  assert len(vision_edges(_model())) == 2
  assert len(vision_edges(_model(left_std=0.6))) == 1  # confidence 0.4 < 0.6
  assert vision_edges(None) == []
  assert vision_edges(NS(roadEdges=[_edge(1.0)], roadEdgeStds=[])) == []


def test_inactive_outside_laneless_and_before_the_history_fills():
  red = NGPRED()
  assert _run(red, _model(right_y=0.8), laneless=False)['state'] == 'INACTIVE'
  red = NGPRED()
  assert red.update(_model(right_y=0.8), (0.0, 0.0), 20.0, [], True)['edges_detected'] == 0  # first frame: history not full


def test_states_by_distance():
  assert _run(NGPRED(), _model())['state'] == REDState.MONITORING.name  # edges 1.8 m away
  assert _run(NGPRED(), _model(right_y=0.8))['state'] == REDState.WARNING.name
  assert _run(NGPRED(), _model(right_y=0.4))['state'] == REDState.CRITICAL.name


def test_edge_on_the_right_pushes_left_and_vice_versa():
  # modelV2 y is right-positive: right edge y > 0 -> push left = positive curvature delta
  right = _run(NGPRED(), _model(right_y=0.7))
  assert right['edge_side'] == 1 and curvature_nudge(right) > 0
  left = _run(NGPRED(), _model(left_y=-0.7))
  assert left['edge_side'] == -1 and curvature_nudge(left) < 0


def test_no_nudge_when_far_or_inactive():
  assert curvature_nudge(_run(NGPRED(), _model())) == 0.0
  assert curvature_nudge(_run(NGPRED(), _model(right_y=0.7), laneless=False)) == 0.0


def test_cost_grows_with_proximity_and_speed_and_is_capped():
  edge = RoadEdge('right', [(5.0, 0.9)], RoadEdgeType.UNKNOWN, 0.9)
  near = RoadEdge('right', [(5.0, 0.3)], RoadEdgeType.UNKNOWN, 0.9)
  assert NGPRED.repulsive_cost(0.0, near, 20.0) > NGPRED.repulsive_cost(0.0, edge, 20.0) > 0.0
  assert NGPRED.repulsive_cost(0.0, edge, 30.0) > NGPRED.repulsive_cost(0.0, edge, 0.0)
  assert NGPRED.repulsive_cost(0.0, RoadEdge('right', [(5.0, 2.0)], RoadEdgeType.UNKNOWN, 0.9), 20.0) == 0.0


def test_path_override_pushes_a_path_that_hugs_the_left_edge_to_the_right():
  left = RoadEdge('left', [(5.0, -0.2)], RoadEdgeType.UNKNOWN, 0.9)
  path, active = NGPRED.path_override([(5.0, -0.1), (10.0, -0.1)], [left])
  assert active and all(y > -0.1 for _, y in path)
