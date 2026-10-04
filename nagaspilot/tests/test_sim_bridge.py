import random

from nagaspilot.controls.ngp_ranging import LeadAnchoredRanger, RoadCamera
from nagaspilot.tools.sim_bridge import Actor, Pose, ground_truth_detections, project_box, range_from_box

EGO = Pose(0.0, 0.0, 25.0)


def test_box_projection_round_trips_through_the_flat_ground_ranging():
  for name in ('car', 'truck', 'motorcycle', 'person'):
    for x, y in ((12.0, 0.0), (30.0, 2.0), (60.0, -3.0), (100.0, 1.0)):
      u, v, w, h = project_box(name, x, y)
      rx, ry = range_from_box(u, v, w, h)
      assert abs(rx - x) < 1e-6 and abs(ry - y) < 1e-6


def test_ngp_ranger_recovers_the_true_range_from_a_simulated_box():
  r = LeadAnchoredRanger(RoadCamera())
  for x in (15.0, 40.0, 90.0):
    u, v, w, h = project_box('car', x, 1.5)
    est = r.range_box('car', u * 1928, (v + h / 2) * 1208, h * 1208, 604.0, 964.0)
    assert est.source == 'ground' and abs(est.x - x) < 1e-6 and abs(est.y - 1.5) < 1e-6


def test_detections_are_relative_filtered_by_fov_range_and_carry_boxes():
  actors = [Actor(1, 'car', Pose(40.0, 3.0, 20.0, -1.0)), Actor(2, 'car', Pose(-20.0, 0.0, 25.0)), Actor(3, 'car', Pose(300.0, 0.0, 25.0)),
            Actor(4, 'truck', Pose(10.0, 40.0, 25.0))]
  d = ground_truth_detections(EGO, actors)
  assert [x.track_id for x in d] == [1]                               # behind, too far and far off-axis are dropped
  assert abs(d[0].x - 40.0) < 1e-9 and abs(d[0].vx + 5.0) < 1e-9 and abs(d[0].vy + 1.0) < 1e-9 and d[0].w > 0 and d[0].h > 0


def test_noise_and_dropout_are_seeded_and_bounded():
  actors = [Actor(i, 'car', Pose(30.0 + i, 0.0, 25.0)) for i in range(30)]
  a = ground_truth_detections(EGO, actors, noise=0.05, dropout=0.3, rng=random.Random(5))
  b = ground_truth_detections(EGO, actors, noise=0.05, dropout=0.3, rng=random.Random(5))
  assert a == b and 10 < len(a) < 30
  assert all(abs(d.x - (30.0 + d.track_id)) < 0.4 * d.x for d in a)
