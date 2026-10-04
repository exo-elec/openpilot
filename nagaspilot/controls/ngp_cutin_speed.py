"""Cut-in speed trim: slow down for an object predicted to cross into our lane soon.

Input is tracked objects in the car frame relative to ego: x forward, y LEFT (yRel
convention), vx/vy relative velocity. For an object not yet in the corridor but moving
toward it, predict when it enters and how close and how fast it will be then. If the
time-to-collision after entry, or the headway at entry, is under the criteria, ask for a
lower cruise speed. This only ever tightens the cruise speed (like BRSC/VTSC); the
longitudinal MPC stays responsible for braking, and this has no braking authority.
Objects already inside the corridor are the lead logic's job and are ignored here.
Criteria are per class (`PROFILES`): cars/trucks/buses and two-wheelers (motorcycle, bicycle) differ.
Pure: no cereal, no Params. Default off at the call site.
"""
import math
from dataclasses import dataclass

MIN_V_EGO = 8.0             # m/s, no action in crawling traffic (TJA handles that)
MIN_TARGET_SPEED = 8.3      # m/s, never cut below this (same floor as BRSC)
MIN_RANGE_M, MAX_RANGE_M = 3.0, 80.0
MIN_CONF = 0.5
MAX_SIGMA_FRAC = 0.25       # range uncertainty must be under 25 % of the range
CONFIRM_UPDATES = 2         # consecutive updates the same track must satisfy the criteria
HOLD_S = 2.0                # keep the target this long after the criteria clear


@dataclass(frozen=True)
class Profile:
  """Per-class cut-in criteria. Two-wheelers are narrow, lane-split and move sideways fast, and the
  rider is the vulnerable party, so they trigger earlier, more often and with a stronger response.
  Starting values, NOT validated: tune them from logged routes with nagaspilot/tools/replay_object_guard.py."""
  half_width_m: float       # corridor half width the object centre must be inside to count as "in"
  min_lateral_speed: float  # m/s toward the corridor
  max_enter_s: float        # only objects entering within this time
  ttc_trigger_s: float
  min_headway_s: float
  max_reduction: float      # at most this fraction below the current speed


CAR = Profile(1.5, 0.3, 3.0, 4.0, 1.0, 0.25)
TWO_WHEELER = Profile(1.3, 0.2, 3.0, 5.0, 1.3, 0.30)
PROFILES: dict[str, Profile] = {'car': CAR, 'truck': CAR, 'bus': CAR, 'motorcycle': TWO_WHEELER, 'bicycle': TWO_WHEELER}


@dataclass(frozen=True)
class Obj:
  track_id: int
  x: float
  y: float
  vx: float
  vy: float
  sigma_x: float
  conf: float
  name: str = 'car'


@dataclass(frozen=True)
class CutInResult:
  active: bool
  target_speed: float | None
  ttc: float | None
  track_id: int | None


class PlannedPath:
  """Our planned path in the car frame: lateral offset (left positive) at forward distance x.

  Built from `modelV2.position` by the adapter (y-right flipped once). Beyond the last point the
  offset is held. `PlannedPath.straight()` is the fixed-corridor fallback.
  """

  def __init__(self, xs, ys_left):
    self.xs = [float(v) for v in xs]
    self.ys = [float(v) for v in ys_left]
    ok = len(self.xs) >= 2 and len(self.xs) == len(self.ys) and all(math.isfinite(v) for v in self.xs + self.ys)
    ok = ok and all(b > a for a, b in zip(self.xs, self.xs[1:], strict=False))
    self.valid = ok

  @classmethod
  def straight(cls) -> 'PlannedPath':
    return cls([0.0, 200.0], [0.0, 0.0])

  def y_at(self, x: float) -> float:
    if not self.valid:
      return 0.0
    if x <= self.xs[0]:
      return self.ys[0]
    if x >= self.xs[-1]:
      return self.ys[-1]
    for i in range(1, len(self.xs)):
      if x <= self.xs[i]:
        f = (x - self.xs[i - 1]) / (self.xs[i] - self.xs[i - 1])
        return self.ys[i - 1] + f * (self.ys[i] - self.ys[i - 1])
    return self.ys[-1]


_STEP_S = 0.1


def evaluate(v_ego: float, o: Obj, path: PlannedPath | None = None) -> float | None:
  """Return an urgency (seconds, lower = more urgent) if `o` is a cut-in threat, else None.

  Steps the object forward at constant relative velocity and finds when its centre first lies
  within the class corridor around the planned path, then tests TTC and headway at that moment.
  (For same-direction constant-velocity motion Autoware's collision-time margin is exactly the
  headway at entry, so the headway test covers it.)
  """
  prof = PROFILES.get(o.name)
  if prof is None:
    return None                       # not a cut-in class (pedestrians cross, they do not cut in)
  vals = (o.x, o.y, o.vx, o.vy, o.sigma_x, o.conf, v_ego)
  if not all(math.isfinite(v) for v in vals):
    return None
  if o.conf < MIN_CONF or not (MIN_RANGE_M <= o.x <= MAX_RANGE_M) or o.sigma_x > MAX_SIGMA_FRAC * o.x:
    return None
  path = path or PlannedPath.straight()

  def off(t: float) -> float:        # lateral offset from the planned path at time t
    x = o.x + o.vx * t
    return (o.y + o.vy * t) - path.y_at(x)

  d0 = off(0.0)
  if abs(d0) <= prof.half_width_m:
    return None                       # already in the corridor: lead logic's job
  toward = (abs(d0) - abs(off(0.5))) / 0.5   # m/s toward the path (can include path curvature)
  if toward < prof.min_lateral_speed:
    return None
  t_enter = None
  n = int(round(prof.max_enter_s / _STEP_S))
  for i in range(1, n + 1):
    t = i * _STEP_S
    if o.x + o.vx * t <= 0:
      return None                     # it will be behind us before it arrives
    if abs(off(t)) <= prof.half_width_m:
      t_enter = t
      break
  if t_enter is None:
    return None
  gap = o.x + o.vx * t_enter
  closing = -o.vx
  ttc = gap / closing if closing > 0.3 else math.inf
  headway = gap / max(v_ego, 0.1)
  if ttc <= prof.ttc_trigger_s or headway < prof.min_headway_s:
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

  def update(self, v_ego: float, objs: list[Obj], dt: float, enabled: bool = True, fresh: bool = True,
             path: PlannedPath | None = None) -> CutInResult:
    if not enabled or not fresh or v_ego < MIN_V_EGO:
      self.reset()
      return CutInResult(False, None, None, None)

    best: tuple[float, Obj] | None = None
    for o in objs:
      u = evaluate(v_ego, o, path)
      if u is not None and (best is None or u < best[0]):
        best = (u, o)

    if best is not None:
      urgency, o = best
      self._cand_n = self._cand_n + 1 if o.track_id == self._cand_id else 1
      self._cand_id = o.track_id
      if self._cand_n >= CONFIRM_UPDATES:
        v_obj = max(v_ego + o.vx, 0.0)
        target = max(v_obj, (1.0 - PROFILES[o.name].max_reduction) * v_ego, min(MIN_TARGET_SPEED, v_ego))
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
