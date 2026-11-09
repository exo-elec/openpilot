"""Curve-speed helpers shared by MTSC, VTSC and the map-speed adapter (moved from EOP10's eop_utils / longitudinal_planner).

`calculate_speed_for_curvature`: v = sqrt(a_comfort / kappa), inf below `min_curvature`.
`blend_mtsc_vtsc`: EOP10's MTSC -> VTSC handover (it lived inline in the planner): MTSC alone beyond 200 m, a conservative blend
between 200 and 150 m, VTSC alone inside 150 m; the blend is rate-limited so the target never jumps. Pure.
"""
import math

HANDOVER_START_M = 200.0     # start blending MTSC -> VTSC
HANDOVER_END_M = 150.0       # VTSC takes full control
MAX_BLEND_STEP_MS = 1.5      # m/s per second of rate limit during the handover


def calculate_speed_for_curvature(curvature: float, a_comfort: float, min_curvature: float = 0.001) -> float:
  if curvature < min_curvature:
    return float('inf')
  return (a_comfort / curvature) ** 0.5


def blend_mtsc_vtsc(mtsc: float | None, vtsc: float | None, distance_m: float, v_cruise: float, prev_blended: float | None,
                    dt: float) -> tuple[float | None, float | None]:
  """Return (curve_target, blended_state). `curve_target` is None when neither controller asks for anything.

  Same rules as EOP10's planner: both active inside the transition zone -> the more conservative of (MTSC, VTSC with a tolerance),
  rate-limited relative to the current cruise speed; VTSC alone inside 150 m; MTSC alone beyond 200 m; otherwise the lower active one.
  """
  both = vtsc is not None and mtsc is not None
  if both and distance_m <= HANDOVER_START_M:
    if distance_m >= HANDOVER_END_M:
      blend = min(max((HANDOVER_START_M - distance_m) / (HANDOVER_START_M - HANDOVER_END_M), 0.0), 1.0)
    else:
      blend = 1.0
    target = min(mtsc, vtsc * 1.1) if blend < 0.5 else min(vtsc, mtsc * 1.05)
    if prev_blended is not None:
      step = MAX_BLEND_STEP_MS * dt
      delta = target - v_cruise
      if abs(delta) > step:
        target = v_cruise + math.copysign(step, delta)
    return target, target
  if vtsc is not None and (mtsc is None or distance_m < HANDOVER_END_M):
    return vtsc, None
  if mtsc is not None and distance_m >= HANDOVER_START_M:
    return mtsc, None
  active = [t for t in (vtsc, mtsc) if t is not None]
  return (min(active) if active else None), None
