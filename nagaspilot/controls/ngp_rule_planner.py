"""Rule-based planner: a second, independent driving channel (monod objects + lane geometry -> curvature, accel).

pathd's parallel channel to the learned policy. It does NOT use the policy's plan: it takes lane geometry
(modelV2 lane lines / planned-path fallback, as a left-positive polyline) and tracked objects, and produces
its own command:
  lateral : lane centre + a bounded offset from the PathSelector, followed with pure pursuit -> curvature
  longit. : IDM car-following on the nearest in-lane object (or one predicted to cut in), capped by the
            set speed and a lateral-acceleration curve limit -> accel
How much authority this channel gets (shadow / supervise / primary) is decided elsewhere
(ngp_policy_arbiter.py). Pure: no cereal, no Params. Frames: car frame, x forward, y LEFT.
"""
import math
from dataclasses import dataclass

from nagaspilot.controls.ngp_cutin_speed import PlannedPath, evaluate
from nagaspilot.controls.ngp_cutin_speed import Obj as CutObj
from nagaspilot.controls.ngp_path_selector import PathSelector, PObj

A_LAT_MAX = 2.0           # m/s^2 comfortable lateral acceleration (curve speed limit)
A_ACCEL = 1.5             # m/s^2 IDM max acceleration
B_COMFORT = 2.0           # m/s^2 IDM comfortable braking
A_BRAKE_LIMIT = -4.0      # m/s^2, the planner never asks for more than this
HEADWAY_S = 1.4
MIN_GAP_M = 4.0
IDM_DELTA = 4.0
LANE_CORRIDOR_M = 1.3     # |offset from our path| for "in our lane"
LOOKAHEAD_MIN_M = 8.0
LOOKAHEAD_TIME_S = 0.9
MAX_CURVATURE = 0.05      # 1/m, 20 m radius


@dataclass(frozen=True)
class RuleCmd:
  valid: bool
  curvature: float          # 1/m, left positive
  accel: float              # m/s^2
  speed_target: float       # m/s
  offset_m: float
  lead_id: int | None
  lead_gap: float | None
  reason: str


def _idm(v: float, v0: float, gap: float | None, v_lead: float) -> float:
  free = 1.0 - (v / max(v0, 0.1)) ** IDM_DELTA
  if gap is None:
    return A_ACCEL * free
  dv = v - v_lead
  s_star = MIN_GAP_M + max(v * HEADWAY_S + v * dv / (2.0 * math.sqrt(A_ACCEL * B_COMFORT)), 0.0)
  return A_ACCEL * (free - (s_star / max(gap, 0.5)) ** 2)


def _curvature_pursuit(path: PlannedPath, offset: float, v_ego: float) -> float:
  ld = max(LOOKAHEAD_MIN_M, LOOKAHEAD_TIME_S * v_ego)
  y = path.y_at(ld) + offset
  k = 2.0 * y / (ld * ld + y * y)
  return min(max(k, -MAX_CURVATURE), MAX_CURVATURE)


class RulePlanner:
  def __init__(self):
    self.selector = PathSelector()

  def reset(self) -> None:
    self.selector.reset()

  def plan(self, v_ego: float, set_speed: float, lane: PlannedPath | None, room_left: float, room_right: float,
           objs: list[PObj], radar_leads: list[tuple[float, float, float]] | None = None, fresh: bool = True) -> RuleCmd:
    """`lane`: lane-centre polyline (None/invalid -> invalid command). `radar_leads`: [(dRel, yRel, vLead)] fallback."""
    if lane is None or not lane.valid or not all(math.isfinite(v) for v in (v_ego, set_speed)):
      return RuleCmd(False, 0.0, 0.0, set_speed, 0.0, None, None, 'no lane')
    sel = self.selector.update(v_ego, objs if fresh else [], lane, room_left, room_right, enabled=True, fresh=True)
    curv = _curvature_pursuit(lane, sel.offset_m, v_ego)

    # speed target: set speed, lateral-acceleration limit on the lane curvature ahead, selector slowdown
    kappa_ahead = abs(_curvature_pursuit(lane, 0.0, v_ego))
    v_curve = math.sqrt(A_LAT_MAX / kappa_ahead) if kappa_ahead > 1e-4 else math.inf
    v0 = max(min(set_speed, v_curve, v_ego * sel.speed_factor if sel.speed_factor < 1.0 else math.inf), 0.0)

    # lead: nearest in-lane object ahead of us (path-relative), a predicted cut-in, or a radar lead
    gap = lead_v = lead_id = None
    for o in objs:
      if o.conf < 0.5 or o.x <= 0.0:
        continue
      rel_y = o.y - lane.y_at(o.x)
      in_lane = abs(rel_y) <= LANE_CORRIDOR_M
      cut = (not in_lane) and evaluate(v_ego, CutObj(o.track_id, o.x, rel_y, o.vx, o.vy, max(0.05 * o.x, 0.3), o.conf, o.name), lane) is not None
      if (in_lane or cut) and (gap is None or o.x < gap):
        gap, lead_v, lead_id = o.x, v_ego + o.vx, o.track_id
    if radar_leads:
      for d, y, v_l in radar_leads:
        if d > 0 and abs(y - lane.y_at(d)) <= LANE_CORRIDOR_M and (gap is None or d < gap):
          gap, lead_v, lead_id = d, v_l, None
    accel = _idm(v_ego, v0, gap, lead_v if lead_v is not None else v_ego)
    accel = min(max(accel, A_BRAKE_LIMIT), A_ACCEL)
    return RuleCmd(True, curv, accel, v0, sel.offset_m, lead_id, gap, sel.reason if gap is None else 'follow')
