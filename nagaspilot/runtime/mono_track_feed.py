"""Tracked objects (with velocity) from a `monoDetections` stream that carries positions only.

EOP10's RKNN monod publishes x, y (left positive), class and confidence but leaves vx/vy/sigma at zero, and
the cut-in and path-selector cores need relative velocity. This feed runs the shared Kalman tracker over
those positions and hands the cores Obj / PObj lists. On NGP10 the monod runtime already tracks, so it
does not use this. Pure apart from the message accessors; no Params.
"""
import math
import time

from nagaspilot.controls.ngp_cutin_speed import PROFILES, Obj
from nagaspilot.controls.ngp_object_tracker import Measurement, ObjectTracker, Track, measurement_sigma
from nagaspilot.controls.ngp_path_selector import OBJECT_WIDTH_M, PObj

MIN_X, MAX_X = 1.0, 150.0
MAX_DT, MIN_DT = 0.5, 0.01
KNOWN = set(OBJECT_WIDTH_M) | set(PROFILES)


class MonoTrackFeed:
  def __init__(self, clock=time.monotonic):
    self.tracker = ObjectTracker()
    self._clock = clock
    self._last: float | None = None
    self.tracks: list[Track] = []

  def reset(self) -> None:
    self.__init__(self._clock)

  def update(self, detections, fresh: bool) -> list[Track]:
    """`detections`: iterable with trackId, className, x, y (left +), confidence. Not fresh -> tracker coasts, then drops."""
    now = self._clock()
    dt = MAX_DT if self._last is None else min(max(now - self._last, MIN_DT), MAX_DT)
    self._last = now
    meas = []
    if fresh:
      for d in detections:
        name, x, y, conf = str(d.className), float(d.x), float(d.y), float(d.confidence)
        if name not in KNOWN or not (MIN_X <= x <= MAX_X) or not math.isfinite(y) or conf <= 0.0:
          continue
        sx, sy = measurement_sigma(x)
        meas.append(Measurement(name, x, y, sx, sy, conf))
    self.tracks = self.tracker.update(meas, dt)
    return self.tracks

  def cutin_objects(self) -> list[Obj]:
    return [Obj(t.track_id, t.x, t.y, t.vx, t.vy, t.sigma_x, 0.0 if t.occluded else t.conf, t.name) for t in self.tracks]

  def path_objects(self) -> list[PObj]:
    return [PObj(t.track_id, t.name, t.x, t.y, t.vx, t.vy, 0.0 if t.occluded else t.conf) for t in self.tracks]
