#!/usr/bin/env python3
"""pathd: the planner. Bounded add-on layer plus an optional parallel rule channel, published as `pathAdjust`.

One process name, `pathd`, on every branch: NGP10 registers `nagaspilot.runtime.pathd`; EOP10/01M/02M keep their
`selfdrive.pathd.pathd` and host `SharedPathdHost` inside it with the gridd source. FUSION is not here: gridd (EOP10)
or monod's own tracker (NGP10) deliver the objects.

Reads modelV2 (policy path, lane lines, policy action), carState, radarState and monoDetections and publishes
  - the PathSelector request (offset, speed factor, horizon profile): the protection layer
  - the RulePlanner command and the DPP-selected arbiter mode (ruleValid/ruleCurvature/ruleAccel/dppMode/dppCase):
    the parallel rule channel, applied by controlsd and the planner through RuleChannelConsumer
Objects come from a source adapter (`object_sources`): monod's tracks on NGP10, gridd's fused `stereoObjects` on
EOP10/01M/02M. The same code is the one process `pathd` on every branch; the board's own planner logic joins it as
`Extras` (more proposals into the same arbitration), it does not run a second channel.
The rule channel is idle (dppMode 0) unless `ngp_dpp_max_mode` > 0; the whole process runs only with
`ngp_pathd_enabled` (default off). With no process, a stale message or mode 0 every consumer passes the policy through.
"""
import math
import time
from dataclasses import dataclass, replace

from nagaspilot.controls.ngp_arbiter import Proposal, arbitrate
from nagaspilot.controls.ngp_cutin_speed import PlannedPath
from nagaspilot.controls.ngp_path_selector import PROFILE_DT_S, PathSelector, build_profile
from nagaspilot.runtime.cutin_adapter import cutin_path
from nagaspilot.runtime.object_sources import MonoDetectionsSource
from nagaspilot.runtime.path_adapter import lane_room
from nagaspilot.runtime.rule_channel import EXEC_TIME_BUDGET_S, RuleChannel, RuleOut


class PathD:
  def __init__(self, source=None):
    """`source`: where the objects come from (nagaspilot/runtime/object_sources.py). pathd plans; it does not fuse or track."""
    self.selector = PathSelector()
    self.channel = RuleChannel()
    self.source = source or MonoDetectionsSource()

  def _objects(self, sm):
    return self.source.objects(sm)

  def step(self, sm, v_ego: float):
    objs, fresh = self._objects(sm)
    path = cutin_path(sm) or PlannedPath.straight()
    room = lane_room(sm['modelV2']) if sm.valid.get('modelV2', False) else (0.0, 0.0)
    sel = self.selector.update(v_ego, objs, path, room[0], room[1], enabled=True, fresh=fresh)
    self._last = (objs, fresh, room)
    return sel, room, len(objs)

  def step_rule(self, sm, v_ego: float, set_speed: float, driver_override: bool, ceiling: int, dt: float) -> RuleOut:
    """Call after `step` in the same frame (shares its objects)."""
    objs, fresh, room = self._last
    # the detector's forward-pass time, when a detector publishes it (NGP10 monod); 0 where there is none
    exec_time = float(sm['monoDetections'].modelExecutionTime) if sm.alive.get('monoDetections', False) and sm.valid.get('monoDetections', False) else 0.0
    perception_ok = bool(fresh and exec_time <= EXEC_TIME_BUDGET_S and sm.valid.get('modelV2', False))
    leads = []
    if sm.valid.get('radarState', False):
      rs = sm['radarState']
      leads = [(float(ld.dRel), float(ld.yRel), float(ld.vLead)) for ld in (rs.leadOne, rs.leadTwo) if ld.status]
    return self.channel.step(v_ego, set_speed, sm['modelV2'], objs if fresh else [], leads, room, perception_ok,
                             driver_override, ceiling, dt)


def fill_path_adjust(pa, sel, room, n_objects: int, frame_id: int, v_ego: float = 0.0, rule: RuleOut | None = None) -> None:
  pa.frameId = frame_id
  pa.offsetM = float(sel.offset_m)
  pa.speedFactor = float(min(sel.speed_factor, 1.0))
  pa.minClearanceM = float('nan') if sel.min_clearance_m is None else float(sel.min_clearance_m)
  pa.reason = sel.reason
  pa.roomLeftM, pa.roomRightM = float(room[0]), float(room[1])
  pa.numObjects = int(n_objects)
  pa.horizonDt = PROFILE_DT_S
  offsets, caps = build_profile(sel, v_ego)
  op = pa.init('offsetProfile', len(offsets))
  sp = pa.init('speedCapProfile', len(caps))
  for i, (o, c) in enumerate(zip(offsets, caps, strict=True)):
    op[i] = float(o)
    sp[i] = float(c)
  if rule is not None:
    pa.ruleValid = bool(rule.cmd.valid)
    pa.ruleCurvature = float(rule.cmd.curvature)
    pa.ruleAccel = float(rule.cmd.accel)
    pa.ruleSpeedTarget = float(rule.cmd.speed_target)
    pa.dppMode = int(rule.mode)
    pa.dppCase = rule.case
    pa.disagreeCurvature = float(rule.d_curv)
    pa.disagreeAccel = float(rule.d_accel)




@dataclass(frozen=True)
class Extras:
  """The board's own planner logic as more proposals (EOP10's LatNudge offset, its emergency/LonNudge speed factor)."""
  offset_m: float | None = None
  speed_factor: float | None = None


def extras_from_eop(speed_reduction: float, lon_delta: float, lateral_adjustments, v_ego: float, lookahead_points: int = 1) -> Extras:
  """EOP10's pathd results as Extras. Its lateral adjustments are in the path's y-RIGHT frame (flipped once here) and
  its speed reductions are m/s deltas (negative = slow down). The factor stays inside the shared contract (>= 0.8);
  EOP10's stronger emergency reduction keeps travelling in `enhancedTrajectory.speedAdjustment`."""
  offset = None
  near = [float(v) for v in list(lateral_adjustments)[:max(lookahead_points, 1)] if math.isfinite(float(v))]
  if near:
    strongest = max(near, key=abs)                       # the largest request in the near part of the path
    if abs(strongest) > 0.01:
      offset = -strongest
  total = (float(speed_reduction) if math.isfinite(speed_reduction) else 0.0) + (float(lon_delta) if math.isfinite(lon_delta) else 0.0)
  factor = max(1.0 + total / max(v_ego, 1.0), 0.8) if total < -0.05 else None
  return Extras(offset, factor)


def merge_extras(sel, room, extras: Extras | None):
  """Merge extra proposals into the selector's request with the same tighten-only rules (one arbitration)."""
  if extras is None or (extras.offset_m is None and extras.speed_factor is None):
    return sel
  props = [Proposal('selector', sel.offset_m)]
  if extras.offset_m is not None and math.isfinite(extras.offset_m):
    props.append(Proposal('board', extras.offset_m))
  arb = arbitrate(props, room[0], room[1])
  factor = sel.speed_factor
  if extras.speed_factor is not None and math.isfinite(extras.speed_factor):
    factor = min(factor, max(extras.speed_factor, 0.0))
  reason = 'slow' if factor < 1.0 else ('nudge' if arb.offset_m != 0.0 else sel.reason)
  return replace(sel, offset_m=arb.offset_m, speed_factor=min(factor, 1.0), reason=reason)


class SharedPathdHost:
  """The per-frame work of pathd, identical on every branch. Call `tick` once per loop; it returns True when it published."""

  def __init__(self, params, pm, source=None, clock=time.monotonic, new_message=None, extras_provider=None):
    """`extras_provider(sm, objects, v_ego) -> Extras | None`: the board's proposers when the caller passes none per tick."""
    self.params, self.pm, self._clock, self._new_message = params, pm, clock, new_message
    self.extras_provider = extras_provider
    self.pathd = PathD(source)
    self.ceiling, self._ceiling_t, self._last_t = 0, 0.0, clock()

  def tick(self, sm, extras: Extras | None = None) -> bool:
    if not sm.updated['modelV2'] or not sm.valid['carState']:
      return False
    now = self._clock()
    dt = min(max(now - self._last_t, 0.01), 0.2)
    self._last_t = now
    if now - self._ceiling_t > 1.0:                       # the DPP ceiling can be changed while driving
      self.ceiling, self._ceiling_t = int(self.params.get("ngp_dpp_max_mode") or 0), now
    cs = sm['carState']
    v_ego = float(cs.vEgo)
    sel, room, n = self.pathd.step(sm, v_ego)
    if extras is None and self.extras_provider is not None:
      extras = self.extras_provider(sm, self.pathd._last[0], v_ego)
    sel = merge_extras(sel, room, extras)
    set_speed = float(cs.cruiseState.speed) if cs.cruiseState.speed > 0 else v_ego
    rule = self.pathd.step_rule(sm, v_ego, set_speed, bool(cs.steeringPressed or cs.brakePressed or cs.gasPressed), self.ceiling, dt)
    new_message = self._new_message
    if new_message is None:
      from cereal import messaging
      new_message = messaging.new_message
    msg = new_message('pathAdjust', valid=math.isfinite(sel.offset_m))
    fill_path_adjust(msg.pathAdjust, sel, room, n, sm['modelV2'].frameId, v_ego, rule)
    self.pm.send('pathAdjust', msg)
    return True


def run(source=None) -> None:
  from nagaspilot.runtime.object_sources import GriddSource
  from cereal import messaging
  from cereal.messaging import PubMaster, SubMaster
  from openpilot.common.params import Params

  sm = SubMaster(['modelV2', 'carState', 'radarState', 'monoDetections', 'stereoObjects'], poll='modelV2',
                 ignore_alive=['monoDetections', 'radarState', 'stereoObjects'])
  params = Params()
  provider = None
  if params.get_bool("ngp_pathd_nudges"):        # EOP10's LatNudge/LonNudge/speed-reduction cores as extra proposers, camera-only inputs
    from nagaspilot.runtime.nudge_extras import NudgeExtras
    nudges = NudgeExtras()
    provider = lambda sm, objs, v_ego: nudges.update(sm['modelV2'], objs, v_ego)  # noqa: E731
  host = SharedPathdHost(params, PubMaster(['pathAdjust']), source or GriddSource(), extras_provider=provider)
  while True:
    sm.update()
    host.tick(sm)


def main():
  run()


if __name__ == "__main__":
  main()
