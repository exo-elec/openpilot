"""Wiring of the parallel rule channel: pathd side (RuleChannel) and consumer side (RuleChannelConsumer).

pathd side  : builds the lane polyline from modelV2, runs RulePlanner, measures the disagreement with the policy
              action in modelV2, derives the DPP Situation and lets DPP pick the arbiter mode inside the
              ceiling `ngp_dpp_max_mode` (0 = rule channel idle).
consumer    : controlsd (curvature) and the planner (accel) each own a PolicyArbiter and apply the mode that
              pathd published to THEIR real policy value. No message, a stale message or an idle channel means
              the policy value passes through unchanged.
Adapters only; the policy of every decision lives in nagaspilot/controls/.
"""
import math
from dataclasses import dataclass

from nagaspilot.controls.ngp_cutin_speed import CutInSpeed, Obj, PlannedPath, evaluate
from nagaspilot.controls.ngp_dpp import DPP, Situation
from nagaspilot.controls.ngp_path_selector import LONG_WINDOW_M, PObj
from nagaspilot.controls.ngp_policy_arbiter import Mode, PolicyArbiter
from nagaspilot.controls.ngp_rule_planner import RuleCmd, RulePlanner

EXEC_TIME_BUDGET_S = 0.15
LANE_PROB_MIN = 0.5
DISAGREE_CURV = 0.006        # 1/m, per-frame disagreement with the policy that counts for DPP
DISAGREE_ACCEL = 2.5         # m/s^2
DISAGREE_DEBOUNCE_S = 0.5
VRU = {'motorcycle', 'bicycle', 'person'}
LARGE = {'truck', 'bus'}
ALONGSIDE_LAT_M = 3.5
CUT_CLASSES_MIN_CONF = 0.5


def lane_polyline(model) -> tuple[PlannedPath | None, float]:
  """Lane-centre polyline (left positive) and the minimum inner-line confidence. Falls back to the planned path."""
  try:
    ll, probs = model.laneLines, model.laneLineProbs
    pl, pr = float(probs[1]), float(probs[2])
    conf = min(pl, pr)
    if conf >= LANE_PROB_MIN:
      xs = [float(v) for v in ll[1].x]
      centre = [-0.5 * (float(a) + float(b)) for a, b in zip(ll[1].y, ll[2].y, strict=True)]   # model y-right -> left
      path = PlannedPath(xs, centre)
      if path.valid:
        return path, conf
  except (AttributeError, IndexError, ValueError):
    conf = 0.0
  try:
    pos = model.position
    path = PlannedPath([float(v) for v in pos.x], [-float(v) for v in pos.y])
    return (path if path.valid else None), 0.0
  except (AttributeError, IndexError):
    return None, 0.0


@dataclass(frozen=True)
class RuleOut:
  cmd: RuleCmd
  mode: int
  case: str
  disagree: bool
  d_curv: float
  d_accel: float


class RuleChannel:
  """pathd side. `step` is called once per modelV2 frame."""

  def __init__(self):
    self.planner = RulePlanner()
    self.dpp = DPP()
    self._dis_t = 0.0

  def reset(self) -> None:
    self.__init__()

  def step(self, v_ego: float, set_speed: float, model, tracks: list[PObj], radar_leads: list[tuple[float, float, float]],
           room: tuple[float, float], perception_ok: bool, driver_override: bool, ceiling: int, dt: float) -> RuleOut:
    if ceiling <= 0:
      self.reset()
      return RuleOut(RuleCmd(False, 0.0, 0.0, set_speed, 0.0, None, None, 'idle'), 0, 'off', False, 0.0, 0.0)
    lane, lane_conf = lane_polyline(model)
    cmd = self.planner.plan(v_ego, set_speed, lane, room[0], room[1], tracks, radar_leads)
    try:
      p_curv, p_acc = float(model.action.desiredCurvature), float(model.action.desiredAcceleration)
    except AttributeError:
      p_curv = p_acc = 0.0
    d_curv, d_acc = (cmd.curvature - p_curv, cmd.accel - p_acc) if cmd.valid else (0.0, 0.0)
    over = abs(d_curv) > DISAGREE_CURV or abs(d_acc) > DISAGREE_ACCEL
    self._dis_t = self._dis_t + dt if over else max(self._dis_t - dt, 0.0)

    cut_in = False
    vru = large = False
    path = lane if lane is not None else PlannedPath.straight()
    for o in tracks:
      if o.conf < CUT_CLASSES_MIN_CONF or o.x <= -LONG_WINDOW_M:
        continue
      rel_y = o.y - path.y_at(max(o.x, 0.0))
      if abs(o.x) < LONG_WINDOW_M and abs(rel_y) < ALONGSIDE_LAT_M:
        vru |= o.name in VRU
        large |= o.name in LARGE
      if o.x > 0.0 and evaluate(v_ego, Obj(o.track_id, o.x, rel_y, o.vx, o.vy, max(0.05 * o.x, 0.3), o.conf, o.name), path) is not None:
        cut_in = True
    sit = Situation(v_ego, perception_ok, cmd.valid, lane_conf, abs(cmd.curvature), cmd.lead_gap, cut_in, vru, large,
                    self._dis_t >= DISAGREE_DEBOUNCE_S, driver_override)
    dec = self.dpp.update(sit, ceiling, dt)
    return RuleOut(cmd, int(dec.mode), dec.case, sit.disagree, d_curv, d_acc)


class RuleChannelConsumer:
  """controlsd / planner side: applies pathd's mode to this process's own policy value."""

  def __init__(self):
    self.lat = PolicyArbiter()
    self.long = PolicyArbiter()

  def curvature(self, mode: int, policy_curv: float, rule_valid: bool, rule_curv: float, fresh: bool,
                driver_override: bool, dt: float) -> tuple[float, str]:
    """Return (curvature, source). Unchanged unless the mode gives the rule channel lateral authority."""
    if mode not in (Mode.PRIMARY_LAT, Mode.PRIMARY_BOTH) or not math.isfinite(rule_curv):
      self.lat.reset()
      return policy_curv, 'policy'
    cmd = RuleCmd(bool(rule_valid), rule_curv, 0.0, 0.0, 0.0, None, None, 'msg')
    b = self.lat.update(mode, policy_curv, 0.0, cmd, fresh, driver_override, dt)
    return b.curvature, b.source

  def accel(self, mode: int, policy_accel: float, rule_valid: bool, rule_accel: float, fresh: bool,
            driver_override: bool, dt: float) -> tuple[float, str]:
    """Return (accel, source). SUPERVISE and the PRIMARY modes with longitudinal authority act here."""
    if mode not in (Mode.SUPERVISE, Mode.PRIMARY_LONG, Mode.PRIMARY_BOTH) or not math.isfinite(rule_accel):
      self.long.reset()
      return policy_accel, 'policy'
    cmd = RuleCmd(bool(rule_valid), 0.0, rule_accel, 0.0, 0.0, None, None, 'msg')
    b = self.long.update(mode, 0.0, policy_accel, cmd, fresh, driver_override, dt)
    return b.accel, b.source


def _fresh(sm) -> bool:
  return bool(sm.alive.get('pathAdjust', False) and sm.valid.get('pathAdjust', False))


def apply_curvature(consumer: RuleChannelConsumer, sm, policy_curv: float, lat_active: bool, driver_override: bool, dt: float) -> float:
  """One-line hook for controlsd: the policy curvature, or the rule channel's blend when DPP gave it lateral authority."""
  if not lat_active or not _fresh(sm):
    consumer.lat.reset()
    return policy_curv
  pa = sm['pathAdjust']
  return consumer.curvature(int(pa.dppMode), policy_curv, bool(pa.ruleValid), float(pa.ruleCurvature), True, driver_override, dt)[0]


def apply_accel(consumer: RuleChannelConsumer, sm, policy_accel: float, dt: float) -> float:
  """One-line hook for the planner: the policy accel, or the blend DPP's mode asks for."""
  if not _fresh(sm):
    consumer.long.reset()
    return policy_accel
  pa, cs = sm['pathAdjust'], sm['carState']
  return consumer.accel(int(pa.dppMode), policy_accel, bool(pa.ruleValid), float(pa.ruleAccel), True,
                        bool(cs.brakePressed or cs.gasPressed), dt)[0]
