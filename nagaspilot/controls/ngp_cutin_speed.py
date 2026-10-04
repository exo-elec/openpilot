"""Cut-in speed trim: slow down for an object predicted to cross into our lane soon.

Input is tracked objects in the car frame relative to ego: x forward, y LEFT (yRel
convention), vx/vy relative velocity. For an object not yet in the corridor but moving
toward it, predict when it enters and how close and how fast it will be then. If the
time-to-collision after entry, or the headway at entry, is under the criteria, ask for a
lower cruise speed. This only ever tightens the cruise speed (like BRSC/VTSC); the
longitudinal MPC stays responsible for braking, and this has no braking authority.
Objects already inside the corridor are the lead logic's job and are ignored here.
Pure: no cereal, no Params. Default off at the call site.
"""
import math
from dataclasses import dataclass

HALF_WIDTH_M = 1.5          # corridor half width (lane ~3.0 m minus a margin for width)
MIN_V_EGO = 8.0             # m/s, no action in crawling traffic (TJA handles that)
MIN_TARGET_SPEED = 8.3      # m/s, never cut below this (same floor as BRSC)
MAX_REDUCTION = 0.25        # at most 25 % below the current speed
MAX_ENTER_S = 3.0           # only objects entering within this time
MIN_LATERAL_SPEED = 0.3     # m/s toward the corridor
TTC_TRIGGER_S = 4.0
MIN_HEADWAY_S = 1.0
MIN_RANGE_M, MAX_RANGE_M = 3.0, 80.0
MIN_CONF = 0.5
MAX_SIGMA_FRAC = 0.25       # range uncertainty must be under 25 % of the range
CONFIRM_UPDATES = 2         # consecutive updates the same track must satisfy the criteria
HOLD_S = 2.0                # keep the target this long after the criteria clear


@dataclass(frozen=True)
class Obj:
  track_id: int
  x: float
  y: float
  vx: float
  vy: float
  sigma_x: float
  conf: float


@dataclass(frozen=True)
class CutInResult:
  active: bool
  target_speed: float | None
  ttc: float | None
  track_id: int | None


def evaluate(v_ego: float, o: Obj) -> float | None:
  """Return the TTC-like urgency (seconds) if `o` is a cut-in threat, else None."""
  vals = (o.x, o.y, o.vx, o.vy, o.sigma_x, o.conf, v_ego)
  if not all(math.isfinite(v) for v in vals):
    return None
  if o.conf < MIN_CONF or not (MIN_RANGE_M <= o.x <= MAX_RANGE_M) or o.sigma_x > MAX_SIGMA_FRAC * o.x:
    return None
  if abs(o.y) <= HALF_WIDTH_M:
    return None                       # already in the corridor: lead logic's job
  toward = -math.copysign(1.0, o.y) * o.vy  # positive when moving toward the centre line
  if toward < MIN_LATERAL_SPEED:
    return None
  t_enter = (abs(o.y) - HALF_WIDTH_M) / toward
  if t_enter > MAX_ENTER_S:
    return None
  gap = o.x + o.vx * t_enter
  if gap <= 0:
    return None                       # it will be behind us when it arrives
  closing = -o.vx
  ttc = gap / closing if closing > 0.3 else math.inf
  headway = gap / max(v_ego, 0.1)
  if ttc <= TTC_TRIGGER_S or headway < MIN_HEADWAY_S:
    return t_enter + min(ttc, headway * 4.0)
  return None


class CutInSpeed:
  def __init__(self):
    self._cand_id: int | None = None
    self._cand_n = 0
    self._hold = 0.0
    self._target: float | None = None
    self._last: tuple[float | None, int | None] = (None, None)

  def reset(self) -> None:
    self.__init__()

  def update(self, v_ego: float, objs: list[Obj], dt: float, enabled: bool = True, fresh: bool = True) -> CutInResult:
    if not enabled or not fresh or v_ego < MIN_V_EGO:
      self.reset()
      return CutInResult(False, None, None, None)

    best: tuple[float, Obj] | None = None
    for o in objs:
      u = evaluate(v_ego, o)
      if u is not None and (best is None or u < best[0]):
        best = (u, o)

    if best is not None:
      urgency, o = best
      self._cand_n = self._cand_n + 1 if o.track_id == self._cand_id else 1
      self._cand_id = o.track_id
      if self._cand_n >= CONFIRM_UPDATES:
        v_obj = max(v_ego + o.vx, 0.0)
        target = max(v_obj, (1.0 - MAX_REDUCTION) * v_ego, min(MIN_TARGET_SPEED, v_ego))
        target = min(target, v_ego)
        self._target = target if self._target is None else min(self._target, target)  # only tighten while held
        self._hold = HOLD_S
        self._last = (urgency, o.track_id)
    else:
      self._cand_id, self._cand_n = None, 0
      self._hold = max(self._hold - dt, 0.0)
      if self._hold == 0.0:
        self._target = None
        self._last = (None, None)

    if self._target is None:
      return CutInResult(False, None, None, None)
    return CutInResult(True, self._target, self._last[0], self._last[1])
