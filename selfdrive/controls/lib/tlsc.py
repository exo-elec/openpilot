"""TLSC (Traffic Light Speed Control) on EOP10: reads its param and stereoObjects, runs the shared core.

The policy is exopilot/selfdrive/controls/lib/tlsc.py, the one TLSC (owner 2026-11-12: one implementation per feature). This adapter reads
TLSCEnabled every 5 s, as EOP10's former copy did, and turns stereoObjects (gridd, from monod's traffic-light detections) into
the core's (state, confidence, distance) list.
"""
from __future__ import annotations

import time

from openpilot.common.params import Params
from exopilot.selfdrive.controls.lib import tlsc as base
from exopilot.selfdrive.controls.lib.tlsc import TLSC_CONFIDENCE, TLSC_DECEL, TLSC_MAX_DIST, TLSC_MIN_DIST  # noqa: F401  (re-exported)


class TLSC(base.TLSC):
  PARAM_REFRESH_S = 5.0

  def __init__(self) -> None:
    self._params = Params()
    super().__init__(enabled=self._params.get_bool("TLSCEnabled"))
    self._last_param_t = 0.0

  def update(self, sm) -> float | None:
    now = time.monotonic()
    if now - self._last_param_t >= self.PARAM_REFRESH_S:
      self._last_param_t = now
      self.enabled = self._params.get_bool("TLSCEnabled")
    lights = [(obj.trafficLightState, obj.trafficLightConfidence, obj.dRel) for obj in sm['stereoObjects'].objects] if self.enabled else []
    return self.update_from(bool(sm['radarState'].leadOne.status), sm['carState'].vEgo, lights)
