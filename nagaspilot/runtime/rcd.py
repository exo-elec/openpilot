"""RCD adapter: reads the surface source out of the SubMaster-like `sm` and feeds the pure core (controls/ngp_rcd.py).

Sources, in EOP10's priority order: `surfaceStatus` (surfaced's roughness score), then `monoSegments` (the camera-tier card's road/edge/drivable hint).
Both exist only on EOP hardware; where the service is not in this branch's SERVICE_LIST (NGP10) there is no source and RCD reports "No data source".
Switch: `enabled_key` param (NGP10: `ngp_lon_rcd`; EOP10's shim passes `EOPRCDEnabled` and legacy_filter_bug=True), re-read every 5 s.
"""
import time

from nagaspilot.controls.ngp_rcd import RCD, RCDState, RoadCondition, from_segmentation, from_surface_score

PARAM_REFRESH_S = 5.0


class RCDRuntime:
  def __init__(self, get_bool=None, enabled_key: str = "ngp_lon_rcd", legacy_filter_bug: bool = False, clock=time.monotonic):
    self._get_bool, self._key, self._clock = get_bool or (lambda k: False), enabled_key, clock
    self.core = RCD(legacy_filter_bug=legacy_filter_bug)
    self._t = -1e9
    self.enabled = False

  @staticmethod
  def from_surface(sm):
    if sm.valid.get('surfaceStatus', False):
      s = sm['surfaceStatus']
      if s.hasSurfaceQuality:
        return from_surface_score(s.surfaceQuality.score, s.surfaceQuality.texture)
    return None

  @staticmethod
  def from_segmentation(sm):
    if sm.valid.get('monoSegments', False):
      seg = next((x for x in sm['monoSegments'].segments if x.camera == 'road'), None)
      if seg is not None and seg.hasRoad:
        return from_segmentation(True, seg.hasEdge, seg.hasDrivable)
    return None

  def _result(self, sm):
    result = self.from_surface(sm)
    return result if result is not None else self.from_segmentation(sm)

  def update(self, sm) -> RCDState:
    now = self._clock()
    if now - self._t >= PARAM_REFRESH_S:
      self._t, self.enabled = now, bool(self._get_bool(self._key))
    if not self.enabled:
      return RCDState(RoadCondition.GOOD, 0.0, 0.0, False, "RCD disabled")
    return self.core.update(self._result(sm))
