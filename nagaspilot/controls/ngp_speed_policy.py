"""Portable, non-controlling speed-limit and speed-zone policy."""

from dataclasses import dataclass
from enum import IntEnum

from nagaspilot.speed_zones import SpeedZone, speed_zone


class SpeedLimitSource(IntEnum):
  NONE = 0
  CAR = 1
  MAP = 2
  NAVIGATION = 3


class SpeedLimitPolicy(IntEnum):
  NONE = 0
  CAR = 1
  MAP = 2
  NAVIGATION = 3
  LOWEST = 4
  MAP_NAV_WITH_CAR_FALLBACK = 5


@dataclass(frozen=True)
class SpeedLimitObservation:
  source: SpeedLimitSource
  limit_mps: float
  distance_to_change_m: float | None = None
  valid: bool = True


@dataclass(frozen=True)
class SpeedPolicyResult:
  zone: SpeedZone
  source: SpeedLimitSource
  resolved_limit_mps: float | None
  suggested_cruise_mps: float
  distance_to_change_m: float | None
  control_applied: bool = False


class NGPSpeedPolicy:
  """Resolve available limits without modifying cruise or actuator state."""

  def __init__(self, policy: SpeedLimitPolicy = SpeedLimitPolicy.LOWEST):
    self.policy = SpeedLimitPolicy(policy)

  @staticmethod
  def _usable(observations):
    return tuple(o for o in (observations or ()) if o.valid and o.limit_mps > 0.0)

  def _resolve(self, observations):
    usable = self._usable(observations)
    if self.policy is SpeedLimitPolicy.NONE:
      return None
    wanted = {
      SpeedLimitPolicy.CAR: SpeedLimitSource.CAR,
      SpeedLimitPolicy.MAP: SpeedLimitSource.MAP,
      SpeedLimitPolicy.NAVIGATION: SpeedLimitSource.NAVIGATION,
    }.get(self.policy)
    if wanted is not None:
      candidates = tuple(o for o in usable if o.source is wanted)
    elif self.policy is SpeedLimitPolicy.MAP_NAV_WITH_CAR_FALLBACK:
      candidates = tuple(o for o in usable if o.source in (SpeedLimitSource.MAP, SpeedLimitSource.NAVIGATION))
      if not candidates:
        candidates = tuple(o for o in usable if o.source is SpeedLimitSource.CAR)
    else:
      candidates = usable
    return min(candidates, key=lambda o: o.limit_mps) if candidates else None

  def evaluate(self, v_ego: float, v_cruise: float, observations=()) -> SpeedPolicyResult:
    resolved = self._resolve(observations)
    suggestion = max(0.0, float(v_cruise))
    if resolved is not None:
      suggestion = min(suggestion, resolved.limit_mps)
    return SpeedPolicyResult(
      zone=speed_zone(v_ego),
      source=resolved.source if resolved is not None else SpeedLimitSource.NONE,
      resolved_limit_mps=resolved.limit_mps if resolved is not None else None,
      suggested_cruise_mps=suggestion,
      distance_to_change_m=resolved.distance_to_change_m if resolved is not None else None,
    )
