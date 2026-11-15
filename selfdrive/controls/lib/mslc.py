"""MSLC (Map Speed Limit Control) on EOP10: reads its params and runs the shared core.

The policy is exopilot/selfdrive/controls/lib/mslc.py, the one MSLC (owner 2026-11-12: one implementation per feature). This adapter reads
MSLCEnabled once a second and the per-range offsets (SLCOffset1..7, km/h) and confirmation switches (SLCConfirmLower/Higher)
every 2 s, as EOP10's former copy did, and logs speed-limit changes.
"""
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from exopilot.selfdrive.controls.lib import mslc as base

PARAM_REFRESH_S = 2.0


class MSLC(base.MSLC):
  def __init__(self):
    super().__init__(enabled=False)
    self.params = Params()
    self._cfg_t = -1e9
    self._update_params()
    self._refresh_config(0.0)

  def _update_params(self):
    self.enabled = self.params.get_bool("MSLCEnabled")

  def _refresh_config(self, t: float):
    if t - self._cfg_t < PARAM_REFRESH_S:
      return
    self._cfg_t = t
    self._offset.set_offsets_kph([self.params.get(f"SLCOffset{i + 1}") for i in range(len(self._offset.BUCKET_EDGES_MS) - 1)])
    self._confirmation.configure(self.params.get_bool("SLCConfirmLower"), self.params.get_bool("SLCConfirmHigher"))

  def update(self, limit_ms, next_limit_ms, next_distance_m, v_ego, driver_overriding, t, nav_speed_limit_ms: float = 0.0):
    if self.frame + 1 >= int(1.0 / DT_MDL):
      self.frame = -1                       # the core adds 1 back
      self._update_params()
      self._refresh_config(t)
    prev_alert = self._prev_alert_text
    result = super().update(limit_ms, next_limit_ms, next_distance_m, v_ego, driver_overriding, t, nav_speed_limit_ms)
    if self._prev_alert_text != prev_alert:
      cloudlog.info(f"MSLC: {self._prev_alert_text}")
    return result
