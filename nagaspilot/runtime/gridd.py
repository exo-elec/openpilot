#!/usr/bin/env python3
"""gridd: the PERCEPTION layer. monod senses, gridd tracks, pathd plans.

On the NGP10 base it is deliberately small: it can only fuse what a single forward camera gives it. It is the same
`gridd` as EOP10's: EOP10's perception code (stereo, BEV occupancy, segmentation, radars, side
cameras) carries over into it as more sources and more steps behind the same message, nothing else changes.

Reads monod's untracked `monoDetections` and publishes the same fused-object message EOP10's gridd publishes
(`stereoObjects`: dRel/yRel left positive, `vRel`, `vyRel`, class, probability, a stable track id) so pathd reads one
interface everywhere. It runs the shared Kalman tracker (`CameraTrackAnnotator`) and publishes only CONFIRMED tracks. If
monod goes stale it publishes nothing, so a missing detector is never mistaken for an empty road. Process name `gridd`; runs with
monod (`ngp_monod_enabled`).
"""
import math
import time

from nagaspilot.runtime.fusion_tracks import CAMERA_CLASSES, CameraTrackAnnotator

OBSTACLE_OF_CLASS = {'car': 'vehicle', 'truck': 'vehicle', 'bus': 'vehicle', 'motorcycle': 'motorcycle', 'bicycle': 'motorcycle', 'person': 'person'}


def detections_to_objects(detections) -> list[dict]:
  out = []
  for d in detections:
    name = str(d.className)
    if name not in CAMERA_CLASSES or float(d.confidence) <= 0.0 or not (math.isfinite(d.x) and math.isfinite(d.y)):
      continue
    out.append({'dRel': float(d.x), 'yRel': float(d.y), 'obstacleType': name, 'confidence': float(d.confidence),
                'trackId': int(d.trackId), 'source': str(d.cameraSource) or 'road', 'vRel': 0.0})
  return out


def fill_stereo_objects(so, objs: list[dict]) -> None:
  items = so.init('objects', len(objs))
  for i, o in enumerate(objs):
    it = items[i]
    it.trackId = int(o['trackId'])
    it.dRel, it.yRel, it.vRel, it.vyRel = float(o['dRel']), float(o['yRel']), float(o['vRel']), float(o['vyRel'])
    it.prob = float(o['confidence'])
    it.obstacleType = OBSTACLE_OF_CLASS[o['obstacleType']]


class Gridd:
  def __init__(self, clock=time.monotonic):
    self.annotator = CameraTrackAnnotator(clock)

  def tick(self, sm, pm, new_message=None) -> bool:
    """One frame: returns True when it published."""
    if not sm.updated['monoDetections']:
      return False
    fresh = bool(sm.valid.get('monoDetections', False))
    objs = detections_to_objects(sm['monoDetections'].detections) if fresh else []
    self.annotator.annotate(objs)                       # also advances the tracker when monod is stale (it coasts, then drops)
    if not fresh:
      return False
    if new_message is None:
      from cereal import messaging
      new_message = messaging.new_message
    confirmed = [o for o in objs if 'vyRel' in o]       # only tracks the Kalman filter has confirmed
    msg = new_message('stereoObjects', valid=True)
    fill_stereo_objects(msg.stereoObjects, confirmed)
    pm.send('stereoObjects', msg)
    return True


def run() -> None:
  from cereal.messaging import PubMaster, SubMaster

  sm = SubMaster(['monoDetections'], poll='monoDetections')
  pm = PubMaster(['stereoObjects'])
  gridd = Gridd()
  while True:
    sm.update()
    gridd.tick(sm, pm)


def main():
  run()


if __name__ == "__main__":
  main()
