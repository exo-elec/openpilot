"""EOP10's RED (an extension of the shared core) vs the pre-refactor implementation.

Equivalent on every input except the two deliberate changes, which have their own tests below:
  1. `edge_side` follows the curvature convention for modelV2's right-positive y (the old value pointed at the edge).
  2. modelV2 edges carry `roadEdgeStds`, not `prob`; confidence now comes from the std, and an edge that vision alone is
     confident about (>= MIN_FUSED_CONF) is kept even without YOLO/stereo corroboration.
"""
import random
from types import SimpleNamespace as NS

from nagaspilot.tests import red_reference as ref
from openpilot.selfdrive.controls.lib import red as new


class _Params:
  def get_bool(self, key):
    return True


def _patch(monkeypatch):
  monkeypatch.setattr(ref, "Params", _Params)
  monkeypatch.setattr(new, "Params", _Params)


def _edge(y, prob=None, n=8):
  e = NS(x=[float(3 * i) for i in range(1, n + 1)], y=[y] * n)
  if prob is not None:
    e.prob = prob
  return e


def _scene(rng):
  """Per frame: two edges, each either weak (rejected by both) or strong with a YOLO barrier confirming it (kept by both)."""
  edges, dets = [], []
  for side_y in (-rng.uniform(0.2, 2.5), rng.uniform(0.2, 2.5)):
    if rng.random() < 0.5:
      edges.append(_edge(side_y, prob=rng.uniform(0.5, 0.59)))
    else:
      edges.append(_edge(side_y, prob=rng.uniform(0.72, 1.0)))
      dets.append(NS(class_label=rng.choice(['guardrail', 'wall', 'curb']), y=side_y + rng.uniform(-0.3, 0.3)))
  return NS(roadEdges=edges), dets


def test_agrees_with_the_old_implementation_except_edge_side(monkeypatch):
  _patch(monkeypatch)
  old, red = ref.RED(), new.RED()
  rng = random.Random(3)
  kept = 0
  scene = _scene(rng)
  for i in range(3000):
    if i % 7 == 0:  # scenes persist for several frames so the temporal filter can pass
      scene = _scene(rng)
    model, dets = scene
    veh = (0.0, rng.uniform(-0.3, 0.3))
    path = [(float(k), rng.uniform(-1.0, 1.0)) for k in range(5)]
    args = (model, dets, None, veh, rng.uniform(0, 30), path, rng.random() > 0.1)
    a, b = old.update(*args), red.update(*args)
    kept += b['edges_detected']
    assert {k: v for k, v in a.items() if k != 'edge_side'} == {k: v for k, v in b.items() if k != 'edge_side'}, i
    if a.get('edge_side', 0) != 0:
      assert b['edge_side'] == -a['edge_side'], i
  assert kept > 100  # the sequence really exercised detection, so agreement is not vacuous


def test_edge_side_points_away_from_the_edge_in_the_right_positive_frame(monkeypatch):
  _patch(monkeypatch)
  for edge_y, expected in ((0.7, 1), (-0.7, -1)):  # right edge -> push left (+1); left edge -> push right (-1)
    red = new.RED()
    model = NS(roadEdges=[_edge(edge_y, prob=0.95)])
    dets = [NS(class_label='guardrail', y=edge_y)]
    out = None
    for _ in range(5):
      out = red.update(model, dets, None, (0.0, 0.0), 20.0, [], True)
    assert out['edge_side'] == expected


def test_real_modelv2_edges_use_the_std_and_work_without_yolo_or_stereo(monkeypatch):
  _patch(monkeypatch)
  model = NS(roadEdges=[_edge(0.7), _edge(1.8)], roadEdgeStds=[0.05, 0.05])  # XYZTData has no `prob`
  red, old = new.RED(), ref.RED()
  out = old_out = None
  for _ in range(5):
    out = red.update(model, None, None, (0.0, 0.0), 20.0, [], True)
    old_out = old.update(model, None, None, (0.0, 0.0), 20.0, [], True)
  assert old_out['edges_detected'] == 0  # the old code skipped every real edge (it looked for `.prob`)
  assert out['edges_detected'] >= 1 and out['state'] == 'WARNING'
