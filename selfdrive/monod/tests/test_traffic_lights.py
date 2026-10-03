#!/usr/bin/env python3
"""Traffic lights: monod's road YOLO → colour → monoDetections → gridd stereoObjects → TLSC."""

from __future__ import annotations

import sys
import unittest  # noqa: TID251
from types import SimpleNamespace
from unittest.mock import MagicMock, patch  # noqa: TID251

import cv2  # noqa: F401  (loaded before the stubbed imports below)
import numpy as np

from cereal import log
from openpilot.selfdrive.controls.lib.radar_zones import RadarZoneMonitor
from openpilot.selfdrive.sided.simple_tracker import SideObject
from openpilot.selfdrive.sided.yolo_detector import (RELEVANT_COCO_CLASSES, ROAD_COCO_CLASSES, TRAFFIC_LIGHT,
                                                      YoloDetector)

with patch.dict(sys.modules, {m: sys.modules.get(m, MagicMock()) for m in ('cereal.messaging', 'msgq', 'msgq.visionipc')}):
  from openpilot.selfdrive.gridd import gridd
  from openpilot.selfdrive.monod.monod import CameraLens, RKNNMonoProcessor, objects_to_road_frame, split_traffic_lights


def _light(bbox, conf=0.7):
  return SideObject(uid=-1, label=TRAFFIC_LIGHT, confidence=conf, distance_m=0.0, lateral_m=0.0,
                    height_m=1.0, velocity_mps=0.0, bbox_2d=bbox, width_m=0.4, length_m=0.4)


class TestDetection(unittest.TestCase):

  def test_traffic_lights_only_on_the_road_camera(self):
    head = np.array([[0.5, 0.5, 0.1, 0.2, 0.9, 9]], dtype=np.float32)  # [N, 6], class 9
    self.assertEqual(YoloDetector.parse_outputs({'output': head}, (1080, 1920)), [])
    found = YoloDetector.parse_outputs({'output': head}, (1080, 1920), ROAD_COCO_CLASSES)
    self.assertEqual([d.label for d in found], [TRAFFIC_LIGHT])
    self.assertNotIn(9, RELEVANT_COCO_CLASSES)  # sided/reard never see them

  def test_monod_road_detector_asks_for_traffic_lights(self):
    made = []
    def factory(daemon, core_id, name, classes=None):
      made.append((name, classes))
      return MagicMock(is_available=True)
    RKNNMonoProcessor(has_tele=True, detector_factory=factory)
    self.assertEqual(dict(made), {'road': ROAD_COCO_CLASSES, 'tele': None})

  def test_horizontal_signal_head_ranged_by_its_long_side(self):
    w = 1920
    focal = (w / 2) / np.tan(np.radians(CameraLens.ROAD_8MM.fov_deg) / 2)
    vertical = _light((950, 400, 970, 400 + focal / 40))    # 1 m tall at 40 m
    horizontal = _light((950, 400, 950 + focal / 40, 420))  # 1 m wide at 40 m
    for obj in (vertical, horizontal):
      (det,) = objects_to_road_frame([obj], (1080, w), CameraLens.ROAD_8MM)
      self.assertAlmostEqual(det['distance_m'], 40.0, delta=0.5)

  def test_colour_and_split(self):
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    frame[20:60, 20:40] = (0, 0, 255)  # red lamp (BGR)
    dets = [{'class': 'car', 'bbox': (100, 100, 150, 150)},
            {'class': TRAFFIC_LIGHT, 'bbox': (20, 20, 40, 60), 'confidence': 0.7}]
    users, lights = split_traffic_lights(frame, dets)
    self.assertEqual([d['class'] for d in users], ['car'])
    self.assertEqual(lights[0]['tl_state'], 1)
    self.assertGreater(lights[0]['tl_conf'], 0.5)


class TestHandOver(unittest.TestCase):

  def test_mono_detection_carries_the_colour(self):
    det = log.Event.new_message().init('monoDetections').init('detections', 1)[0]
    det.className, det.trafficLightState, det.trafficLightConfidence = TRAFFIC_LIGHT, 2, 0.4
    self.assertEqual(det.trafficLightState, 2)

  def test_every_detector_class_is_a_valid_obstacle_type(self):
    """capnp raises on an unknown enumerant: "car" and "lead" used to kill gridd."""
    obj = log.StereoObjects.new_message().init('objects', 1)[0]
    for name in [*ROAD_COCO_CLASSES.values(), 'lead', 'something new', 0]:
      obj.obstacleType = gridd.obstacle_type(name)
    obj.obstacleType = gridd.obstacle_type(TRAFFIC_LIGHT)
    self.assertEqual(str(obj.obstacleType), 'trafficLight')
    obj.obstacleType = gridd.obstacle_type('car')
    self.assertEqual(str(obj.obstacleType), 'vehicle')

  def test_gridd_keeps_light_state_and_range(self):
    host = gridd.GridD.__new__(gridd.GridD)
    light = SimpleNamespace(x=40.0, y=1.0, z=0.0, className=TRAFFIC_LIGHT, confidence=0.7, trackId=0,
                            cameraSource='road', trafficLightState=1, trafficLightConfidence=0.3)
    stereo_point_near_but_not_on_it = np.array([[0.0, 0.0, 38.0]])
    (obj,) = host._fuse_mono_detections(SimpleNamespace(detections=[light]), stereo_point_near_but_not_on_it)
    self.assertEqual((obj['dRel'], obj['trafficLightState']), (40.0, 1))

  def test_lead_under_a_light_does_not_replace_it(self):
    host = gridd.GridD.__new__(gridd.GridD)
    light = {'dRel': 40.0, 'yRel': 0.0, 'obstacleType': TRAFFIC_LIGHT, 'confidence': 0.5, 'trafficLightState': 1}
    lead = {'dRel': 40.5, 'yRel': 0.0, 'obstacleType': 'lead', 'confidence': 0.9}
    merged = host._merge_detections([light], [lead])
    self.assertEqual(sorted(o['obstacleType'] for o in merged), ['lead', TRAFFIC_LIGHT])
    self.assertEqual(light['trafficLightState'], 1)

  def test_radar_zones_ignore_traffic_lights(self):
    monitor = RadarZoneMonitor()
    light = SimpleNamespace(trackId=0, dRel=2.0, yRel=2.5, vRel=0.0, prob=0.9, laneZone=2,
                            obstacleType='trafficLight')
    carstate = SimpleNamespace(leftBlindspot=False, rightBlindspot=False, vEgo=0.0, gearShifter='drive')
    monitor.update(SimpleNamespace(objects=[light]), carstate, None, None, 1.0)
    self.assertEqual(monitor.last_fused_objects, [])


if __name__ == '__main__':
  unittest.main()
