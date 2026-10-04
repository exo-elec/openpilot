"""monoDetections -> cut-in objects for the planner (adapter; the policy is nagaspilot/controls/ngp_cutin_speed.py)."""
from nagaspilot.controls.ngp_cutin_speed import Obj, PlannedPath


def cutin_objects(sm) -> tuple[list[Obj], bool]:
  """Return (objects, fresh). Not fresh when monod is not running or its message is stale/invalid.

  Coasting (occluded) tracks arrive with confidence 0 and are dropped by the policy's confidence gate.
  """
  fresh = bool(sm.alive.get('monoDetections', False) and sm.valid.get('monoDetections', False))
  if not fresh:
    return [], False
  return [Obj(int(d.trackId), float(d.x), float(d.y), float(d.vx), float(d.vy), float(d.sigmaX), float(d.confidence), str(d.className))
          for d in sm['monoDetections'].detections], True


def cutin_path(sm) -> PlannedPath | None:
  """Planned path from modelV2.position. The model is y-RIGHT; flip once to the left-positive car frame."""
  if not sm.valid.get('modelV2', False):
    return None
  pos = sm['modelV2'].position
  if len(pos.x) < 2 or len(pos.y) != len(pos.x):
    return None
  path = PlannedPath(list(pos.x), [-float(v) for v in pos.y])
  return path if path.valid else None
