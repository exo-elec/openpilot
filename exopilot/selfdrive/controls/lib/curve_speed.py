"""Curve-speed helpers shared by MTSC, VTSC and the map-speed adapter (moved from EOP10's speed_utils / longitudinal_planner).

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


def blend_mtsc_vtsc(mtsc: float | None, vtsc: float | None, distance_m: float, v_cruise: float, prev_both: bool | None,
                    dt: float) -> tuple[float | None, bool]:
  """Return (curve_target, both). `curve_target` is None when neither controller asks for anything; pass `both` back next frame.

  Same rules as EOP10's planner: both active inside the transition zone -> the more conservative of (MTSC, VTSC with a tolerance),
  rate-limited relative to the current cruise speed whenever the previous frame also had both targets (so the first frame in the
  zone cannot jump either); VTSC alone inside 150 m; MTSC alone beyond 200 m; otherwise the lower active one.
  """
  both = vtsc is not None and mtsc is not None
  if both and distance_m <= HANDOVER_START_M:
    if distance_m >= HANDOVER_END_M:
      blend = min(max((HANDOVER_START_M - distance_m) / (HANDOVER_START_M - HANDOVER_END_M), 0.0), 1.0)
    else:
      blend = 1.0
    target = min(mtsc, vtsc * 1.1) if blend < 0.5 else min(vtsc, mtsc * 1.05)
    if prev_both:
      step = MAX_BLEND_STEP_MS * dt
      delta = target - v_cruise
      if abs(delta) > step:
        target = v_cruise + math.copysign(step, delta)
    return target, both
  if vtsc is not None and (mtsc is None or distance_m < HANDOVER_END_M):
    return vtsc, both
  if mtsc is not None and distance_m >= HANDOVER_START_M:
    return mtsc, both
  active = [t for t in (vtsc, mtsc) if t is not None]
  return (min(active) if active else None), both


EARTH_RADIUS_M = 6371000.0


def _ground_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  """Distance on the ground (m) between two nearby points (equirectangular: exact enough over a few km)."""
  x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2.0))
  return EARTH_RADIUS_M * math.hypot(x, math.radians(lat2 - lat1))


def curves_from_path(position, path) -> list[tuple[float, float]]:
  """(distance ahead m, |curvature| 1/m) along mapd's path (mapdExtendedOut.path / .position).

  The path can start behind us: it is followed from the point nearest to `position`, and distances add up along it.
  """
  points = list(path)
  if not points:
    return []
  lat, lon = position.latitude, position.longitude
  start = min(range(len(points)), key=lambda i: _ground_m(lat, lon, points[i].latitude, points[i].longitude))
  out, d = [], _ground_m(lat, lon, points[start].latitude, points[start].longitude)
  for i in range(start, len(points)):
    if i > start:
      d += _ground_m(points[i - 1].latitude, points[i - 1].longitude, points[i].latitude, points[i].longitude)
    out.append((d, abs(float(points[i].curvature))))
  return out
