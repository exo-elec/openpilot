#!/usr/bin/env python3
"""pathd add-on layer at the NGP10 base: bounded lateral offset + speed factor around the policy path.

NGP10 base only: EOP10/01M/02M do not register this process (EOP10 has its own `selfdrive.pathd` daemon; map `pathAdjust` onto `enhancedTrajectory` through an adapter instead).
Reads modelV2 (policy path, lane lines), carState and monoDetections; runs the pure PathSelector;
publishes `pathAdjust`. Publish-only: nothing consumes it until the shadow replay proves it
(`python3 -m nagaspilot.tools.replay_object_guard`). Runs only with `ngp_pathd_enabled` (default off).
"""
import math

from nagaspilot.controls.ngp_cutin_speed import PlannedPath
from nagaspilot.controls.ngp_path_selector import PROFILE_DT_S, PathSelector, build_profile
from nagaspilot.runtime.cutin_adapter import cutin_path
from nagaspilot.runtime.path_adapter import lane_room, pobjects


class PathD:
  def __init__(self):
    self.selector = PathSelector()

  def step(self, sm, v_ego: float):
    objs, fresh = pobjects(sm)
    path = cutin_path(sm) or PlannedPath.straight()
    room = lane_room(sm['modelV2']) if sm.valid.get('modelV2', False) else (0.0, 0.0)
    sel = self.selector.update(v_ego, objs, path, room[0], room[1], enabled=True, fresh=fresh)
    return sel, room, len(objs)


def fill_path_adjust(pa, sel, room, n_objects: int, frame_id: int, v_ego: float = 0.0) -> None:
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


def main():
  from cereal import messaging
  from cereal.messaging import PubMaster, SubMaster

  sm = SubMaster(['modelV2', 'carState', 'monoDetections'], poll='modelV2', ignore_alive=['monoDetections'])
  pm = PubMaster(['pathAdjust'])
  pathd = PathD()
  while True:
    sm.update()
    if not sm.updated['modelV2'] or not sm.valid['carState']:
      continue
    v_ego = float(sm['carState'].vEgo)
    sel, room, n = pathd.step(sm, v_ego)
    msg = messaging.new_message('pathAdjust', valid=math.isfinite(sel.offset_m))
    fill_path_adjust(msg.pathAdjust, sel, room, n, sm['modelV2'].frameId, v_ego)
    pm.send('pathAdjust', msg)


if __name__ == "__main__":
  main()
