import numpy as np

from nagaspilot.controls.ngp_detect import LIGHT_CLASSES, Box
from nagaspilot.runtime.monod import detect_lights, fill_raw_detections
from nagaspilot.runtime.traffic_light import classify_rgb, head_position


def frame_with_lamp(rgb, size=(40, 24)):
  img = np.full((200, 300, 3), 30, np.uint8)
  img[60:60 + size[0], 100:100 + size[1]] = rgb
  return img


def test_lamp_colour_classification_and_unknown():
  assert classify_rgb(frame_with_lamp((255, 20, 20)), (95, 55, 130, 105))[0] == 1
  assert classify_rgb(frame_with_lamp((255, 200, 0)), (95, 55, 130, 105))[0] == 2
  assert classify_rgb(frame_with_lamp((20, 255, 60)), (95, 55, 130, 105))[0] == 3
  assert classify_rgb(frame_with_lamp((30, 30, 30)), (95, 55, 130, 105)) == (0, 0.0)
  assert classify_rgb(frame_with_lamp((255, 20, 20)), (500, 500, 600, 600)) == (0, 0.0)          # box outside the frame


def test_head_range_from_size_and_limits():
  b = Box(9, 'traffic light', 0.8, 100.0, 50.0, 124.0, 90.0)             # 40 px long side
  rng, y = head_position(b, 2648.0, 160.0)
  assert abs(rng - 2648.0 / 40.0) < 1e-9 and y > 0                      # left of the column
  assert head_position(Box(9, 'traffic light', 0.8, 0.0, 0.0, 2.0, 2.0), 2648.0, 160.0) is None     # too far
  assert head_position(Box(9, 'traffic light', 0.8, 0.0, 0.0, 900.0, 900.0), 2648.0, 160.0) is None  # too close


def test_detect_lights_filters_to_lights_and_fill_raw_publishes_them():
  img = frame_with_lamp((255, 20, 20))
  boxes = [Box(9, 'traffic light', 0.8, 95.0, 55.0, 130.0, 105.0), Box(2, 'car', 0.9, 10.0, 100.0, 80.0, 180.0)]
  lights = detect_lights(boxes, img, 2648.0, 150.0, 300, 200)
  assert len(lights) == 1 and lights[0].state == 1

  class B:
    def init(self, n, k):
      setattr(self, n, [B() for _ in range(k)])
      return getattr(self, n)
  md = B()
  fill_raw_detections(md, [], 5, 1.0, 0.01, lights)
  d = md.detections[0]
  assert d.className == 'traffic light' and d.trafficLightState == 1 and d.x > 0 and d.trackId == 0 and md.numTracks == 0
  assert 9 in LIGHT_CLASSES
