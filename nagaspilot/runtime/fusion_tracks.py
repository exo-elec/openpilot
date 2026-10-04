"""Velocity for fused camera objects, computed where the fusion is (gridd), not in the planner.

gridd's camera objects (monod boxes fused with stereo) leave `vRel` at 0 and carry no lateral speed, so a planner
cannot predict a cut-in from them. `CameraTrackAnnotator` runs the shared Kalman tracker over gridd's object dicts and writes the
relative speed (`vRel`, only where gridd has none) and the lateral speed (`vyRel`) back onto them. Objects from radar
keep their measured Doppler `vRel`. Pure: dicts in, dicts annotated; no cereal.
"""
import math
import time

from nagaspilot.controls.ngp_object_tracker import Measurement, ObjectTracker, measurement_sigma

CAMERA_CLASSES = {'car', 'truck', 'bus', 'motorcycle', 'bicycle', 'person'}
MIN_X, MAX_X = 1.0, 150.0
MAX_DT, MIN_DT = 0.5, 0.01
MATCH_X_M, MATCH_Y_M = 3.0, 1.5


class CameraTrackAnnotator:
  def __init__(self, clock=time.monotonic):
    self.tracker = ObjectTracker()
    self._clock = clock
    self._last: float | None = None

  def annotate(self, objects: list[dict]) -> int:
    """Annotate `objects` in place; return how many got a velocity. Call once per fusion frame."""
    now = self._clock()
    dt = MAX_DT if self._last is None else min(max(now - self._last, MIN_DT), MAX_DT)
    self._last = now
    cam = []
    for o in objects:
      name = str(o.get('obstacleType', '')).lower()
      x, y = float(o.get('dRel', 0.0)), float(o.get('yRel', 0.0))
      if 'source' in o and name in CAMERA_CLASSES and MIN_X <= x <= MAX_X and math.isfinite(y):
        cam.append((o, name, x, y))
    meas = []
    for _, name, x, y in cam:
      sx, sy = measurement_sigma(x)
      meas.append(Measurement(name, x, y, sx, sy, 1.0))
    tracks = self.tracker.update(meas, dt)
    n = 0
    for o, name, x, y in cam:
      best = None
      for t in tracks:
        if t.name != name or t.occluded:
          continue
        dx, dy = abs(t.x - x), abs(t.y - y)
        if dx <= MATCH_X_M and dy <= MATCH_Y_M and (best is None or dx + dy < best[0]):
          best = (dx + dy, t)
      if best is None:
        continue
      t = best[1]
      if not o.get('vRel'):
        o['vRel'] = float(t.vx)
      o['vyRel'] = float(t.vy)
      n += 1
    return n
