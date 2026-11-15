"""MTSC (Map Turn Speed Control) on EOP10: reads its params and runs the shared core.

The policy is exopilot/selfdrive/controls/lib/mtsc.py, the one MTSC (owner 2026-11-12: one implementation per feature). This adapter only
does what the core leaves to the runtime: read MTSCEnabled / MapdEnabled / CurveSpeedLearnEnabled / TSCTargetLatAccel once a
second, as EOP10's former copy did, and look learned curve speeds up in the curve database.
"""
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from exopilot.selfdrive.controls.lib import mtsc as base
from exopilot.selfdrive.controls.lib.speed_utils import get_learned_speed

MTSCState = base.MTSCState


class MTSC(base.MTSC):
  def __init__(self):
    super().__init__(enabled=False, learned_speed=lambda lat, lon, curvature: get_learned_speed(lat, lon, curvature, True, min_speed_kph=20.0))
    self.params = Params()
    self._update_params()

  def _update_params(self):
    self.enabled = self.params.get_bool("MTSCEnabled")
    self.mapd_enabled = self.params.get_bool("MapdEnabled")
    self.curve_learn_enabled = self.params.get_bool("CurveSpeedLearnEnabled")
    try:
      self.a_comfort = float(self.params.get("TSCTargetLatAccel") or 1.8)
    except (ValueError, TypeError):
      self.a_comfort = 1.8

  def update(self, curves, v_ego: float, lat: float = 0.0, lon: float = 0.0) -> dict:
    if self.frame % int(1.0 / DT_MDL) == 0:
      self._update_params()
    return super().update(curves, v_ego, lat, lon)
