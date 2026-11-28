"""monoDetections -> cut-in objects for the planner (adapter; the policy is exopilot/selfdrive/controls/lib/cutin_speed.py)."""
from exopilot.selfdrive.controls.lib.cutin_speed import Obj, PlannedPath


def _sm_valid(sm, key: str) -> bool:
  valid = getattr(sm, 'valid', None)
  return bool(valid.get(key, False)) if valid is not None else (key in sm)


def _sm_alive(sm, key: str) -> bool:
  alive = getattr(sm, 'alive', None)
  return bool(alive.get(key, False)) if alive is not None else (key in sm)


def cutin_objects(sm) -> tuple[list[Obj], bool]:
  """Return (objects, fresh). Not fresh when monod is not running or its message is stale/invalid.

  Coasting (occluded) tracks arrive with confidence 0 and are dropped by the policy's confidence gate.
  """
  fresh = bool(_sm_alive(sm, 'monoDetections') and _sm_valid(sm, 'monoDetections'))
  if not fresh:
    return [], False
  return [Obj(int(d.trackId), float(d.x), float(d.y), float(d.vx), float(d.vy), float(d.sigmaX), float(d.confidence), str(d.className))
          for d in sm['monoDetections'].detections], True


def cutin_path(sm) -> PlannedPath | None:
  """Planned path from modelV2.position. The model is y-RIGHT; flip once to the left-positive car frame."""
  if not _sm_valid(sm, 'modelV2'):
    return None
  pos = sm['modelV2'].position
  if len(pos.x) < 2 or len(pos.y) != len(pos.x):
    return None
  path = PlannedPath(list(pos.x), [-float(v) for v in pos.y])
  return path if path.valid else None
