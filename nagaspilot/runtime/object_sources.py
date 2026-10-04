"""Where pathd gets its objects: one interface, two sources. pathd is the planner; FUSION lives upstream.

  MonoDetectionsSource : NGP10. monod already tracks (x, y, relative vx/vy, sigma, confidence) and is the only camera.
  GriddSource          : EOP10 / 01M / 02M. gridd is the fusion daemon (mono + stereo + radar + side cameras); its
                         `stereoObjects` carry the fused position, relative speed `vRel` and lateral speed `vyRel`.

Both return `(list[PObj], fresh)`; pathd and everything behind it (selector, rule planner, cut-in, DPP) are the same
code on every branch. pathd does no tracking and no sensor fusion itself.
"""
from nagaspilot.controls.ngp_path_selector import PObj
from nagaspilot.runtime.path_adapter import pobjects

OBSTACLE_NAMES = {'vehicle': 'car', 'motorcycle': 'motorcycle', 'person': 'person'}
MIN_PROB = 0.5


class MonoDetectionsSource:
  name = 'monoDetections'

  def objects(self, sm) -> tuple[list[PObj], bool]:
    return pobjects(sm)


class GriddSource:
  name = 'gridd'

  def objects(self, sm) -> tuple[list[PObj], bool]:
    fresh = bool(sm.alive.get('stereoObjects', False) and sm.valid.get('stereoObjects', False))
    if not fresh:
      return [], False
    out = []
    for o in sm['stereoObjects'].objects:
      name = OBSTACLE_NAMES.get(str(o.obstacleType))
      if name is None or float(o.prob) < MIN_PROB:
        continue                                   # traffic lights, bumps, radar-only points: not for the cut-in / selector logic
      out.append(PObj(int(o.trackId), name, float(o.dRel), float(o.yRel), float(o.vRel), float(getattr(o, 'vyRel', 0.0)), float(o.prob)))
    return out, True
