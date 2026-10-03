#!/usr/bin/env python3
"""monod: RKNN road + telephoto YOLO, projection, and fusion."""

from __future__ import annotations

import sys
import unittest  # noqa: TID251
from unittest.mock import MagicMock, patch  # noqa: TID251

import numpy as np

from openpilot.selfdrive.sided.simple_tracker import SideObject

# monod pulls in cereal.messaging at import; the code under test needs none of it.
with patch.dict(sys.modules, {'cereal.messaging': sys.modules.get('cereal.messaging', MagicMock())}):
  from openpilot.selfdrive.monod import monod
  from openpilot.selfdrive.monod.monod import (CameraLens, MultiCameraFusion, RKNNMonoProcessor,
                                               objects_to_road_frame)


def _obj(label, bbox, conf=0.8, height_m=1.5):
  return SideObject(uid=-1, label=label, confidence=conf, distance_m=0.0, lateral_m=0.0,
                    height_m=height_m, velocity_mps=0.0, bbox_2d=bbox, width_m=1.8, length_m=4.5)


class _FakeDetector:
  FAULT_THRESHOLD = 3

  def __init__(self, daemon, core_id, name, classes=None):
    self.daemon, self.core_id, self.name, self.classes = daemon, core_id, name, classes
    self.is_available = True
    self.is_fault = False
    self.consecutive_failures = 0
    self.objects = []

  def detect(self, frame):
    return self.objects


class TestRknnMonoProcessor(unittest.TestCase):

  def test_road_and_tele_both_on_rknn_last_core(self):
    made = []
    def factory(*a, **kw):
      made.append(_FakeDetector(*a, **kw))
      return made[-1]
    proc = RKNNMonoProcessor(has_tele=True, detector_factory=factory)
    self.assertEqual([d.name for d in made], ['road', 'tele'])
    last_core = monod.get_platform_npu_config().core_count - 1
    self.assertEqual({d.core_id for d in made}, {last_core})
    self.assertTrue(proc.tele_available)

  def test_no_tele_board_builds_no_tele_detector(self):
    proc = RKNNMonoProcessor(has_tele=False, detector_factory=_FakeDetector)
    self.assertFalse(proc.tele_available)
    self.assertEqual(proc.infer_yolo_tele(np.zeros((1080, 1920, 3), dtype=np.uint8)), [])

  def test_more_than_three_road_users(self):
    """monod's point: every road user, not openpilot's three leads."""
    proc = RKNNMonoProcessor(detector_factory=_FakeDetector)
    proc._road.objects = [_obj('car', (100 + i * 250, 500, 220 + i * 250, 600)) for i in range(6)]
    dets = proc.infer_yolo_road(np.zeros((1080, 1920, 3), dtype=np.uint8))
    self.assertEqual(len(dets), 6)

  def test_fault_reason(self):
    proc = RKNNMonoProcessor(detector_factory=_FakeDetector)
    self.assertEqual(proc.fault_reason, "")
    proc._road.is_available = False
    self.assertEqual(proc.fault_reason, "npu_unavailable")


class TestRoadFrame(unittest.TestCase):

  def test_centre_is_straight_ahead(self):
    d = objects_to_road_frame([_obj('car', (900, 500, 1020, 620))], (1080, 1920), CameraLens.ROAD_8MM)[0]
    self.assertAlmostEqual(d['lateral_m'], 0.0, places=6)

  def test_right_of_image_is_negative_lateral(self):
    right = objects_to_road_frame([_obj('car', (1700, 500, 1800, 600))], (1080, 1920), CameraLens.ROAD_8MM)[0]
    self.assertLess(right['lateral_m'], 0.0)

  def test_tele_reaches_past_road_camera(self):
    # 1.5 m tall car, 53 px tall on the 16mm tele: ~150 m out
    d = objects_to_road_frame([_obj('car', (940, 500, 980, 553))], (1080, 1920), CameraLens.TELE_16MM)
    self.assertEqual(len(d), 1)
    self.assertGreater(d[0]['distance_m'], CameraLens.ROAD_8MM.max_range_m)
    road = objects_to_road_frame([_obj('car', (940, 500, 980, 553))], (1080, 1920), CameraLens.ROAD_8MM)
    self.assertAlmostEqual(road[0]['distance_m'], 74.6, delta=1.0)

  def test_wide_lens_is_equidistant(self):
    d = objects_to_road_frame([_obj('car', (1880, 500, 1920, 700))], (1080, 1920), CameraLens.WIDE_1_7MM)
    bearing = np.degrees(np.arctan2(-d[0]['lateral_m'], d[0]['distance_m']))
    self.assertAlmostEqual(bearing, 73.4, delta=1.0)


class TestFusion(unittest.TestCase):

  ROAD = [{'class': 'car', 'confidence': 0.7, 'distance_m': 80.0, 'lateral_m': 0.5}]

  def test_tele_only_far_track(self):
    tracks = MultiCameraFusion().fuse_detections([], [{'class': 'truck', 'confidence': 0.6,
                                                       'distance_m': 160.0, 'lateral_m': 1.0}])
    self.assertEqual([t.sources for t in tracks], [['tele']])

  def test_tele_confirms_road_track(self):
    tele = [{'class': 'car', 'confidence': 0.6, 'distance_m': 82.0, 'lateral_m': 0.0}]
    tracks = MultiCameraFusion().fuse_detections(self.ROAD, tele)
    self.assertEqual(len(tracks), 1)
    self.assertEqual(tracks[0].sources, ['road', 'tele'])
    self.assertAlmostEqual(tracks[0].confidence, 0.8)

  def test_road_track_persists_across_frames(self):
    fusion = MultiCameraFusion()
    first = fusion.fuse_detections(self.ROAD)
    second = fusion.fuse_detections(self.ROAD)
    self.assertEqual(first[0].track_id, second[0].track_id)


class TestBoardHasTele(unittest.TestCase):

  def test_follows_running_board(self):
    for has in (True, False):
      hw = MagicMock()
      hw.get_camera_array_config.return_value = {'has_tele_road': has}
      with patch.object(monod, 'HARDWARE', hw):
        self.assertEqual(monod.board_has_tele(), has)

  def test_board_without_camera_config(self):
    hw = MagicMock()
    hw.get_camera_array_config.return_value = {}
    with patch.object(monod, 'HARDWARE', hw):
      self.assertFalse(monod.board_has_tele())


if __name__ == '__main__':
  unittest.main()
