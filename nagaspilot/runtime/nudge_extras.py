"""EOP10's pathd proposers (LatNudge, LonNudge, trajectory speed reduction) running on camera-only inputs, as Extras.

The same cores EOP10's pathd runs (`eop_lat_nudge`, `eop_lon_nudge`, `eop_speed_reduction`), fed from what any branch has:
  - lane boundaries at 0, 5 ... 30 m from the `modelV2` inner lane lines (y-right frame, like EOP10's stereo boundaries);
    an unseen line means no lateral nudge;
  - tracked objects from gridd (PObj: dRel = x, yRel = y, vRel = vx);
  - no stereo ground, no occupancy: `drivable_limit_m` is infinite, so LonNudge acts only through its lead term.
Output is `Extras` for SharedPathdHost, merged by the same tighten-only arbitration. On EOP10 the same `Extras` come from
its own pathd (`extras_from_eop`); the cores are the shared ones.
"""
import math
from types import SimpleNamespace as NS

from nagaspilot.controls.eop_lat_nudge import BOUNDARY_DISTANCES, LatNudge
from nagaspilot.controls.eop_lon_nudge import LonNudge
from nagaspilot.controls.eop_speed_reduction import compute_speed_reduction

LINE_PROB_MIN = 0.5
MAX_DEPTH_M = 80.0


def _at(xs, ys, x: float) -> float:
  if x <= xs[0]:
    return float(ys[0])
  for i in range(1, len(xs)):
    if x <= xs[i]:
      f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
      return float(ys[i - 1] + f * (ys[i] - ys[i - 1]))
  return float(ys[-1])


def boundaries_from_model(model) -> tuple[list[float], list[float]] | None:
  """(left, right) lateral positions at BOUNDARY_DISTANCES in the y-right frame, or None when a line is not trusted."""
  try:
    ll, pr = model.laneLines, model.laneLineProbs
    if float(pr[1]) < LINE_PROB_MIN or float(pr[2]) < LINE_PROB_MIN:
      return None
    xs = [float(v) for v in ll[1].x]
    left = [_at(xs, [float(v) for v in ll[1].y], d) for d in BOUNDARY_DISTANCES]
    right = [_at(xs, [float(v) for v in ll[2].y], d) for d in BOUNDARY_DISTANCES]
  except (AttributeError, IndexError, ValueError):
    return None
  return (left, right) if all(a < 0.0 < b for a, b in zip(left, right, strict=True)) else None


class NudgeExtras:
  def __init__(self, legacy_scale_bug: bool = False, max_depth_m: float = MAX_DEPTH_M):
    self.lat, self.lon = LatNudge(), LonNudge()
    self.lat.enabled = self.lon.enabled = True
    self.legacy, self.max_depth = legacy_scale_bug, max_depth_m

  def update(self, model, objs, v_ego: float):
    """`objs`: PObj list. Returns the pathd `Extras`."""
    from nagaspilot.runtime.pathd import extras_from_eop
    tracks = [NS(trackId=o.track_id, dRel=o.x, yRel=o.y, vRel=o.vx, prob=o.conf) for o in objs if o.conf >= 0.5 and math.isfinite(o.x + o.y + o.vx)]
    lateral = [0.0]
    b = boundaries_from_model(model)
    if b is not None:
      lateral = self.lat.update(b[0], b[1], tracks, v_ego)
    else:
      self.lat.update([-1.8] * 7, [1.8] * 7, [], v_ego)          # keep the smoothing state decaying, no obstacle logic
      lateral = [0.0]
    lon_delta = self.lon.update(math.inf, False, False, tracks, v_ego, 0.0)
    reduction = compute_speed_reduction(tracks, None, v_ego, self.max_depth, legacy_scale_bug=self.legacy)
    return extras_from_eop(reduction, lon_delta, lateral, v_ego, lookahead_points=4)      # 0, 5, 10, 15 m
