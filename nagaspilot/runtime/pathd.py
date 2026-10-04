#!/usr/bin/env python3
"""pathd on the NGP10 base: bounded add-on layer plus an optional parallel rule channel, published as `pathAdjust`.

NGP10 base only: EOP10/01M/02M do not register this process under the name `pathd` (EOP10 has its own
`selfdrive.pathd`); EOP10 runs it as `pathadjustd` with a MonoTrackFeed (see eop_pathadjustd.py).

Reads modelV2 (policy path, lane lines, policy action), carState, radarState and monoDetections and publishes
  - the PathSelector request (offset, speed factor, horizon profile): the protection layer
  - the RulePlanner command and the DPP-selected arbiter mode (ruleValid/ruleCurvature/ruleAccel/dppMode/dppCase):
    the parallel rule channel, applied by controlsd and the planner through RuleChannelConsumer
The rule channel is idle (dppMode 0) unless `ngp_dpp_max_mode` > 0; the whole process runs only with
`ngp_pathd_enabled` (default off). With no process, a stale message or mode 0 every consumer passes the policy through.
"""
import math

from nagaspilot.controls.ngp_cutin_speed import PlannedPath
from nagaspilot.controls.ngp_path_selector import PROFILE_DT_S, PathSelector, build_profile
from nagaspilot.runtime.cutin_adapter import cutin_path
from nagaspilot.runtime.path_adapter import lane_room, pobjects
from nagaspilot.runtime.rule_channel import EXEC_TIME_BUDGET_S, RuleChannel, RuleOut


class PathD:
  def __init__(self, feed=None):
    """`feed`: optional MonoTrackFeed for sources whose monoDetections carry positions only (EOP10)."""
    self.selector = PathSelector()
    self.channel = RuleChannel()
    self.feed = feed

  def _objects(self, sm):
    if self.feed is None:
      return pobjects(sm)
    fresh = bool(sm.alive.get('monoDetections', False) and sm.valid.get('monoDetections', False))
    self.feed.update(sm['monoDetections'].detections if fresh else [], fresh)
    return self.feed.path_objects(), fresh

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
    exec_time = float(sm['monoDetections'].modelExecutionTime) if fresh and self.feed is None else 0.0
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


def run(feed=None) -> None:
  import time

  from cereal import messaging
  from cereal.messaging import PubMaster, SubMaster
  from openpilot.common.params import Params

  params = Params()
  sm = SubMaster(['modelV2', 'carState', 'radarState', 'monoDetections'], poll='modelV2',
                 ignore_alive=['monoDetections', 'radarState'])
  pm = PubMaster(['pathAdjust'])
  pathd = PathD(feed)
  ceiling, ceiling_t, last_t = 0, 0.0, time.monotonic()
  while True:
    sm.update()
    if not sm.updated['modelV2'] or not sm.valid['carState']:
      continue
    now = time.monotonic()
    dt = min(max(now - last_t, 0.01), 0.2)
    last_t = now
    if now - ceiling_t > 1.0:                       # the DPP ceiling can be changed while driving
      ceiling, ceiling_t = int(params.get("ngp_dpp_max_mode") or 0), now
    cs = sm['carState']
    v_ego = float(cs.vEgo)
    sel, room, n = pathd.step(sm, v_ego)
    set_speed = float(cs.cruiseState.speed) if cs.cruiseState.speed > 0 else v_ego
    rule = pathd.step_rule(sm, v_ego, set_speed, bool(cs.steeringPressed or cs.brakePressed or cs.gasPressed), ceiling, dt)
    msg = messaging.new_message('pathAdjust', valid=math.isfinite(sel.offset_m))
    fill_path_adjust(msg.pathAdjust, sel, room, n, sm['modelV2'].frameId, v_ego, rule)
    pm.send('pathAdjust', msg)


def main():
  run()


if __name__ == "__main__":
  main()
