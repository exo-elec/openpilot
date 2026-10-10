"""Portable planar BSD view. No Exopilot hardware, transports or sensor drivers."""
import math


def blindspot_blocked(left: bool, right: bool, direction: str) -> bool:
  """Vehicle CAN flags are presence-only; never invent range or velocity."""
  return bool(left) if direction == 'left' else bool(right) if direction == 'right' else False


def ground_range(range_m: float, elevation_deg: float) -> float:
  """Project a measured 3D slant range onto the horizontal BSD plane."""
  if not math.isfinite(range_m) or range_m < 0 or not math.isfinite(elevation_deg) or abs(elevation_deg) > 90:
    return math.nan
  return range_m * math.cos(math.radians(elevation_deg))
