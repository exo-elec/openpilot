"""Compatibility adapter for the shared pure camera lead-handoff policy."""

from openpilot.common.params import Params
from nagaspilot.controls.ngp_lc_lead_handoff import NGPLeadHandoff, _LeadProxy


RADAR_TO_CAMERA = 1.52


class LaneChangeLeadHandoff(NGPLeadHandoff):
  """EOP parameter adapter over the shared NGP/EOP handoff implementation."""

  def __init__(self):
    super().__init__(radar_to_camera=RADAR_TO_CAMERA)
    self.enabled = bool(Params().get_bool("EOPLCAdjacentLeadHandoff"))

  @staticmethod
  def _pick_adjacent_lead(model_v2, direction: int, v_ego: float) -> _LeadProxy | None:
    return NGPLeadHandoff(radar_to_camera=RADAR_TO_CAMERA)._pick_adjacent_lead(model_v2, direction, v_ego)

  def update(self, model_v2, radar_state, lc_state: int, lc_dir: int, v_ego: float, now: float):
    return super().update(self.enabled, model_v2, radar_state, lc_state, lc_dir, v_ego, now)
