"""Applying adaptd's published state: accel/decel clamp for the longitudinal controller and a personality recommendation.

Pure helpers for the two consumers (controlsd, selfdrived). Both only ever make the car more conservative or leave it alone:
  - `clamp_accel_limits`: (min_accel, max_accel) tightened by `decelMax`/`accelMax` when they are sane (0 < x < 10)
  - `personality_from_state`: the recommended personality, capped at `max_personality` (NGP10's upstream enum has no `traffic`, so 3 -> 2)
Moved from the inline blocks in EOP10's controlsd / selfdrived (same sanity checks).
"""
import math


def clamp_accel_limits(limits: tuple[float, float], enabled: bool, accel_max: float, decel_max: float) -> tuple[float, float]:
  if not enabled or not (math.isfinite(accel_max) and math.isfinite(decel_max)):
    return limits
  if 0.0 < accel_max < 10.0 and 0.0 < decel_max < 10.0:
    return max(limits[0], -decel_max), min(limits[1], accel_max)
  return limits


def personality_from_state(current: int, enabled: bool, recommended: int, max_personality: int = 3) -> int:
  """The personality to use now (the current one when adaptd is off or its recommendation is out of range)."""
  if not enabled or not (0 <= int(recommended) <= 3):
    return current
  return min(int(recommended), max_personality)
