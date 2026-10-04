import numpy as np

from nagaspilot.controls.ngp_detect import Box
from nagaspilot.runtime.monod import MonoPipeline, fill_detections, vanishing_point

K = np.array([[2648.0, 0, 964.0], [0, 2648.0, 604.0], [0, 0, 1.0]])
VIEW_FROM_CALIB = np.array([[0.0, 1, 0], [0, 0, 1], [1, 0, 0]])   # calibrated x-forward/y-right/z-down -> view x-right/y-down/z-forward
H, W = 1208, 1928


def box_at(dist, name='car', u=964.0, w=120.0):
  bottom = 604.0 + 1.22 * 2648.0 / dist
  return Box(2, name, 0.8, u - w / 2, bottom - 90, u + w / 2, bottom)


class Builder:
  """Duck-typed capnp builder: attribute set + init(list)."""
  def init(self, name, n):
    setattr(self, name, [Builder() for _ in range(n)])
    return getattr(self, name)


def test_vanishing_point_straight_ahead_is_principal_point():
  u, v = vanishing_point(VIEW_FROM_CALIB, K)
  assert abs(u - 964) < 1e-9 and abs(v - 604) < 1e-9


def test_pipeline_ranges_tracks_and_flags_left_positive():
  p = MonoPipeline(K, W, H)
  for i in range(10):
    out = p.step([box_at(40.0 - 0.5 * i, u=964 - 80)], [], VIEW_FROM_CALIB, 0.2)
  assert len(out) == 1
  t = out[0]
  assert abs(t.x - 35.5) < 1.0 and t.y > 0 and t.vx < 0      # left of centre -> +y, closing -> vx < 0


def test_pipeline_learns_scale_from_radar_lead():
  p = MonoPipeline(K, W, H, cam_height=1.22 * 0.85)           # wrong height: 15 % short
  for _ in range(80):
    out = p.step([box_at(50.0)], [(50.0, 0.0)], VIEW_FROM_CALIB, 0.2)
  assert abs(out[0].x - 50.0) < 2.0


def test_fill_detections_fields_and_occluded_confidence():
  p = MonoPipeline(K, W, H)
  for i in range(6):
    tracks = p.step([box_at(30.0)], [], VIEW_FROM_CALIB, 0.2)
  md = Builder()
  fill_detections(md, tracks, 7, 12.5, 0.011)   # before the tracks coast: they are the same objects
  for _ in range(3):
    occluded = p.step([], [], VIEW_FROM_CALIB, 0.2)
  d = md.detections[0]
  assert md.frameId == 7 and md.numTracks == 1 and abs(md.modelExecutionTime - 0.011) < 1e-9
  assert d.className == 'car' and d.cameraSource == 'road' and d.confidence > 0.7 and abs(d.x - 30.0) < 1.0
  md2 = Builder()
  fill_detections(md2, occluded, 8, 12.9, 0.0)
  assert md2.detections[0].confidence == 0.0 and md2.detections[0].sigmaX > d.sigmaX


def test_detect_is_sensing_only_untracked_and_fill_raw_publishes_it():
  from nagaspilot.runtime.monod import fill_raw_detections
  p = MonoPipeline(K, W, H)
  meas = p.detect([box_at(30.0, u=900.0)], [], VIEW_FROM_CALIB)
  assert len(meas) == 1 and abs(meas[0].x - 30.0) < 0.5 and p.tracker.tracks == []        # nothing tracked here
  md = Builder()
  fill_raw_detections(md, meas, 9, 3.5, 0.02)
  d = md.detections[0]
  assert md.frameId == 9 and d.trackId == 0 and d.className == 'car' and d.confidence > 0.7 and d.w > 0 and not hasattr(d, 'vx')
