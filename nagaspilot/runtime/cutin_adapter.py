"""monoDetections -> cut-in objects for the planner (adapter; the policy is nagaspilot/controls/ngp_cutin_speed.py)."""
from nagaspilot.controls.ngp_cutin_speed import Obj


def cutin_objects(sm) -> tuple[list[Obj], bool]:
  """Return (objects, fresh). Not fresh when monod is not running or its message is stale/invalid.

  Coasting (occluded) tracks arrive with confidence 0 and are dropped by the policy's confidence gate.
  """
  fresh = bool(sm.alive.get('monoDetections', False) and sm.valid.get('monoDetections', False))
  if not fresh:
    return [], False
  return [Obj(int(d.trackId), float(d.x), float(d.y), float(d.vx), float(d.vy), float(d.sigmaX), float(d.confidence), str(d.className))
          for d in sm['monoDetections'].detections], True
