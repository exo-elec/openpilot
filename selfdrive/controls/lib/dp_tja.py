"""DragonPilot low-speed traffic-jam gap policy.

The longitudinal MPC remains responsible for collision avoidance and braking.
This policy can only reduce positive acceleration and positive jerk.
"""

from dataclasses import dataclass

from nagaspilot.speed_zones import CITY_SPEED_MPS


@dataclass(frozen=True)
class TJAResult:
  active: bool
  cut_in: bool
  desired_gap: float
  accel_scale: float
  jerk_scale: float


class TrafficJamAssist:
  CUT_IN_HOLD_S = 1.5
  STABLE_LEAD_S = 1.0
  STOPPING_GAP_M = 4.0
  TIME_GAP_S = 1.1

  def __init__(self, dt: float):
    self.dt = dt
    self.lead_seen = False
    self.last_d_rel = 0.0
    self.last_track_id = -1
    self.stable_time = 0.0
    self.cut_in_timer = 0.0

  @staticmethod
  def _value(lead, name, default):
    return getattr(lead, name, default)

  def update(self, v_ego: float, lead) -> TJAResult:
    active = v_ego < CITY_SPEED_MPS and bool(self._value(lead, "status", False))
    desired_gap = self.STOPPING_GAP_M + self.TIME_GAP_S * max(v_ego, 0.0)

    if not active:
      self.lead_seen = False
      self.stable_time = 0.0
      self.cut_in_timer = max(0.0, self.cut_in_timer - self.dt)
      return TJAResult(False, False, desired_gap, 1.0, 1.0)

    d_rel = max(0.0, float(self._value(lead, "dRel", 0.0)))
    v_rel = float(self._value(lead, "vRel", 0.0))
    track_id = int(self._value(lead, "radarTrackId", -1))
    model_prob = float(self._value(lead, "modelProb", 1.0))

    track_changed = self.lead_seen and track_id >= 0 and self.last_track_id >= 0 and track_id != self.last_track_id
    distance_jump = self.lead_seen and d_rel < self.last_d_rel - 2.5
    new_uncertain_lead = not self.lead_seen and model_prob < 0.9
    if track_changed or distance_jump or new_uncertain_lead:
      self.cut_in_timer = self.CUT_IN_HOLD_S

    closing_ttc = d_rel / max(-v_rel, 0.01) if v_rel < 0.0 else float("inf")
    close_or_closing = d_rel <= desired_gap or closing_ttc < 3.0
    stable = not (track_changed or distance_jump) and v_rel >= -0.5 and model_prob >= 0.75
    self.stable_time = self.stable_time + self.dt if stable else 0.0
    self.cut_in_timer = max(0.0, self.cut_in_timer - self.dt)

    if self.cut_in_timer > 0.0 or close_or_closing:
      accel_scale, jerk_scale = 0.0, 0.35
    elif self.stable_time >= self.STABLE_LEAD_S and d_rel >= desired_gap + 2.0:
      accel_scale, jerk_scale = 1.0, 1.0
    else:
      accel_scale, jerk_scale = 0.55, 0.65

    self.lead_seen = True
    self.last_d_rel = d_rel
    self.last_track_id = track_id
    return TJAResult(True, self.cut_in_timer > 0.0, desired_gap, accel_scale, jerk_scale)
