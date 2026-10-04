import math

import numpy as np

from nagaspilot.controls.ngp_object_tracker import (MAX_COAST_S, Measurement, ObjectTracker, measurement_sigma,
                                                    predict_path, time_to_corridor)
from nagaspilot.controls.ngp_ranging import LeadAnchoredRanger, RoadCamera

CAM = RoadCamera()
HORIZON, VP = 604.0, 964.0


def bottom_row(dist, cam=CAM):
  return HORIZON + cam.height_m * cam.focal / dist


def test_ground_range_and_lateral_sign():
  r = LeadAnchoredRanger(CAM)
  out = r.range_box('car', VP + 100, bottom_row(30.0), 80, HORIZON, VP)
  assert out.source == 'ground' and abs(out.x - 30.0) < 1e-6
  assert out.y < 0 and abs(out.y + 100 * 30 / CAM.focal) < 1e-6   # right of the vanishing column -> negative (left-positive frame)
  assert r.range_box('car', VP - 100, bottom_row(30.0), 80, HORIZON, VP).y > 0


def test_height_fallback_when_clipped_or_at_horizon():
  r = LeadAnchoredRanger(CAM)
  out = r.range_box('car', VP, 1200, 120, HORIZON, VP, clipped_bottom=True)
  assert out.source == 'height' and abs(out.x - 1.5 * CAM.focal / 120) < 1e-6
  assert r.range_box('car', VP, HORIZON + 1, 50, HORIZON, VP).source == 'height'
  assert r.range_box('traffic light', VP, HORIZON + 1, 50, HORIZON, VP) is None   # no prior for unknown class


def test_lead_anchor_corrects_biased_geometry():
  r = LeadAnchoredRanger(RoadCamera(height_m=1.22 * 0.9))  # camera height wrong by 10 %: every range reads 10 % short
  true = 40.0
  for _ in range(60):
    est = r.range_box('car', VP, HORIZON + 1.22 * CAM.focal / true, 90, HORIZON, VP)
    r.observe_leads([('car', est)], [(true, 0.0)])
  fixed = r.range_box('truck', VP, HORIZON + 1.22 * CAM.focal / 60.0, 90, HORIZON, VP)  # another class: global scale
  assert abs(fixed.x - 60.0) < 1.0


def test_lead_match_gates_bearing_and_range():
  r = LeadAnchoredRanger(CAM)
  est = r.range_box('car', VP, bottom_row(30.0), 80, HORIZON, VP)
  assert r.observe_leads([('car', est)], [(30.0, 8.0)]) == 0     # different bearing
  assert r.observe_leads([('car', est)], [(90.0, 0.0)]) == 0     # ratio 3
  assert r.observe_leads([('car', est)], [(31.0, 0.1)]) == 1


def meas(x, y, name='car'):
  sx, sy = measurement_sigma(x)
  return Measurement(name, x, y, sx, sy)


def test_tracker_confirms_and_estimates_velocity():
  t = ObjectTracker()
  out = []
  for i in range(30):
    out = t.update([meas(30.0 - 5.0 * 0.05 * i, 0.0)], 0.05)   # closing at 5 m/s
  assert len(out) == 1 and abs(out[0].vx + 5.0) < 0.6 and out[0].track_id == 1


def test_occlusion_coasts_then_drops_and_keeps_id():
  t = ObjectTracker()
  for i in range(20):
    t.update([meas(30.0 - 0.25 * i, 0.0)], 0.05)
  tid = t.tracks[0].track_id
  x0 = t.tracks[0].x
  for _ in range(10):
    out = t.update([], 0.05)
  assert out and out[0].occluded and out[0].x < x0               # coasting forward on its prediction
  for _ in range(int(MAX_COAST_S / 0.05) + 5):
    out = t.update([], 0.05)
  assert out == []                                               # dropped after the timeout
  t2 = ObjectTracker()
  for i in range(20):
    t2.update([meas(30.0 - 0.25 * i, 0.0)], 0.05)
  for _ in range(6):
    t2.update([], 0.05)
  out = t2.update([meas(28.0, 0.0)], 0.05)
  assert out[0].track_id == t2.tracks[0].track_id and not out[0].occluded


def test_classes_not_mixed_and_tentative_dropped():
  t = ObjectTracker()
  for _ in range(5):
    out = t.update([meas(20.0, 0.0, 'car'), meas(20.0, 0.0, 'person')], 0.05)
  assert sorted(o.name for o in out) == ['car', 'person']
  t = ObjectTracker()
  t.update([meas(20.0, 0.0)], 0.05)
  t.update([], 0.05)
  t.update([], 0.05)
  assert t.tracks == []


def test_prediction_and_corridor():
  t = ObjectTracker()
  for i in range(30):
    out = t.update([meas(40.0 - 10.0 * 0.05 * i, 4.0 - 1.0 * 0.05 * i)], 0.05)  # closing 10 m/s, drifting toward the lane at 1 m/s
  tr = out[0]
  path = predict_path(tr)
  assert len(path) == 6 and path[-1][1] < tr.x
  ttc = time_to_corridor(tr, half_width=1.2)
  assert ttc is not None and 0.5 < ttc < 3.0
  tr2 = ObjectTracker()
  for i in range(30):
    o = tr2.update([meas(40.0, 8.0)], 0.05)
  assert time_to_corridor(o[0]) is None
