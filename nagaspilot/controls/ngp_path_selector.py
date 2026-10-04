"""pathd add-on core: pick a small lateral offset from the policy path, or ask for a lower speed.

The driving policy (modeld) gives the path and speeds. This layer samples a few lateral offsets
around that path, predicts every tracked object (car frame, y LEFT, relative velocity) over a
short horizon with constant velocity, and scores each offset by clearance, comfort and deviation.
It is a Frenet-style sampler, not Hybrid A*: no occupancy grid or map is needed, so it runs on a
comma 3. Output is bounded: the offset never exceeds the room we have inside the lane, and the
speed factor only ever lowers speed. It has no braking authority.

Pure: no cereal, no Params. The adapter supplies the path (PlannedPath), tracks and lane room.
"""
import math
from dataclasses import dataclass

from nagaspilot.controls.ngp_cutin_speed import PlannedPath

EGO_WIDTH_M = 1.9
OBJECT_WIDTH_M = {'car': 1.8, 'truck': 2.5, 'bus': 2.5, 'motorcycle': 0.8, 'bicycle': 0.6, 'person': 0.6}
CLEAR_LAT_M = {'car': 0.8, 'truck': 1.0, 'bus': 1.0, 'motorcycle': 1.1, 'bicycle': 1.2, 'person': 1.2}  # wanted lateral gap
LONG_WINDOW_M = 7.0           # |dx| inside this means the two are side by side
HORIZON_S = 3.0
STEP_S = 0.25
OFFSET_STEP_M = 0.1
MAX_NUDGE_M = 0.6             # hard cap on the lateral offset
DEVIATION_WEIGHT = 0.4        # cost per metre of offset
CHANGE_WEIGHT = 0.8           # cost per metre change versus last output (comfort)
COLLISION_COST = 100.0
MIN_CONF = 0.5
MIN_V_EGO = 8.0
SPEED_FACTOR_MIN = 0.80       # never ask for more than 20 % below current speed
MIN_NUDGE_BENEFIT = 0.25      # the best offset must beat zero offset by this much, else stay at zero


@dataclass(frozen=True)
class PObj:
  track_id: int
  name: str
  x: float
  y: float
  vx: float      # relative velocity
  vy: float
  conf: float


@dataclass(frozen=True)
class Selection:
  offset_m: float          # left positive; add to the policy path
  speed_factor: float      # <= 1.0
  min_clearance_m: float | None   # worst lateral clearance at the chosen offset
  cost: float
  reason: str


def _clearance(v_ego: float, path: PlannedPath, offset: float, objs: list[PObj]) -> float | None:
  """Smallest (lateral gap - wanted gap) over the horizon for objects side by side with us. None if none."""
  worst = None
  n = int(round(HORIZON_S / STEP_S))
  for o in objs:
    w = OBJECT_WIDTH_M.get(o.name, 1.8)
    wanted = CLEAR_LAT_M.get(o.name, 0.8)
    v_obj = v_ego + o.vx
    for i in range(n + 1):
      t = i * STEP_S
      ex = v_ego * t
      ey = path.y_at(ex) + offset
      ox = o.x + v_obj * t
      oy = o.y + o.vy * t
      if abs(ox - ex) > LONG_WINDOW_M:
        continue
      gap = abs(oy - ey) - (w + EGO_WIDTH_M) / 2.0
      margin = gap - wanted
      worst = margin if worst is None else min(worst, margin)
  return worst


class PathSelector:
  def __init__(self):
    self.last_offset = 0.0

  def reset(self) -> None:
    self.last_offset = 0.0

  def update(self, v_ego: float, objs: list[PObj], path: PlannedPath | None, room_left_m: float, room_right_m: float,
             enabled: bool = True, fresh: bool = True) -> Selection:
    """`room_*` = metres we may move inside the lane toward that side (from lane lines, >= 0)."""
    if not enabled or not fresh or v_ego < MIN_V_EGO or not math.isfinite(v_ego):
      self.reset()
      return Selection(0.0, 1.0, None, 0.0, 'off')
    path = path or PlannedPath.straight()
    relevant = [o for o in objs if o.conf >= MIN_CONF and all(math.isfinite(v) for v in (o.x, o.y, o.vx, o.vy)) and -LONG_WINDOW_M < o.x < 80.0]
    lo = -min(max(room_right_m, 0.0), MAX_NUDGE_M)
    hi = min(max(room_left_m, 0.0), MAX_NUDGE_M)
    n = int(round((hi - lo) / OFFSET_STEP_M))
    cands = sorted({round(lo + i * OFFSET_STEP_M, 3) for i in range(n + 1)} | {0.0})

    def cost(off: float) -> tuple[float, float | None]:
      c = _clearance(v_ego, path, off, relevant)
      pen = 0.0
      if c is not None and c < -1e-6:
        pen = COLLISION_COST * min(-c, 1.0) + 10.0 * (-c)    # inside the wanted gap: grows as the gap closes
      return pen + DEVIATION_WEIGHT * abs(off) + CHANGE_WEIGHT * abs(off - self.last_offset), c

    base_cost, base_c = cost(0.0)
    best_off, (best_cost, best_c) = 0.0, (base_cost, base_c)
    for off in cands:
      c_cost, c_clear = cost(off)
      if c_cost < best_cost - 1e-9:
        best_off, best_cost, best_c = off, c_cost, c_clear
    if best_off != 0.0 and base_cost - best_cost < MIN_NUDGE_BENEFIT:
      best_off, best_cost, best_c = 0.0, base_cost, base_c

    factor = 1.0
    reason = 'clear'
    if best_c is not None and best_c < -1e-6:
      # no offset inside our room clears it: ask for a bounded slowdown proportional to the shortfall
      factor = max(SPEED_FACTOR_MIN, 1.0 - 0.2 * min(-best_c / CLEAR_LAT_M['car'], 1.0))
      reason = 'slow'
    elif best_off != 0.0:
      reason = 'nudge'
    self.last_offset = best_off
    return Selection(best_off, factor, best_c, best_cost, reason)
