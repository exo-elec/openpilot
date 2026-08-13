#!/usr/bin/env python3
"""Speed Limit Resolver — Topic 05 integration.

Wraps MSLC/NSLC/car dash outputs with user-configurable source policy,
and publishes a unified SpeedLimitState for the UI.

Policy (SpeedLimitPolicy param, int):
  0 = none          — ignore all speed limits
  1 = car           — use dashboard-reported limit only
  2 = map           — use MSLC (OSM) only
  3 = nav           — use NSLC (navigation) only
  4 = both          — use lower of all available sources (MSLC, NSLC, car)
  5 = car_fallback  — prefer MSLC/NSLC, fall back to car dash when unavailable
"""

from dataclasses import dataclass

from openpilot.common.params import Params


@dataclass
class ResolvedLimit:
  source: int
  limit_mps: float
  distance_to_change_m: float
  active: bool


class SpeedLimitResolver:
  SOURCE_NONE = 0
  SOURCE_CAR = 1
  SOURCE_MAP = 2
  SOURCE_NAV = 3

  PARAM_REFRESH_S = 2.0

  def __init__(self):
    self.params = Params()
    self._policy = 4
    self._last_mslc: float | None = None
    self._last_nslc: float | None = None
    self._last_car: float | None = None
    self._last_param_t: float = 0.0

  def _read_params(self):
    import time
    now = time.monotonic()
    if now - self._last_param_t < self.PARAM_REFRESH_S:
      return
    self._last_param_t = now
    try:
      self._policy = int(self.params.get("SpeedLimitPolicy") or "4")
    except (ValueError, TypeError):
      self._policy = 4

  def update(self, mslc_limit_mps: float | None, nslc_limit_mps: float | None,
             car_limit_mps: float | None, v_ego: float,
             distance_to_change_m: float = 0.0) -> ResolvedLimit:
    self._read_params()

    if mslc_limit_mps is not None:
      self._last_mslc = mslc_limit_mps
    if nslc_limit_mps is not None:
      self._last_nslc = nslc_limit_mps
    if car_limit_mps is not None:
      self._last_car = car_limit_mps

    raw_limit: float | None = None
    source = self.SOURCE_NONE

    if self._policy == 0:
      raw_limit = None
    elif self._policy == 1:
      raw_limit = car_limit_mps
      source = self.SOURCE_CAR
    elif self._policy == 2:
      raw_limit = mslc_limit_mps
      source = self.SOURCE_MAP
    elif self._policy == 3:
      raw_limit = nslc_limit_mps
      source = self.SOURCE_NAV
    elif self._policy in (4, 5):
      candidates = []
      if mslc_limit_mps is not None:
        candidates.append((mslc_limit_mps, self.SOURCE_MAP))
      if nslc_limit_mps is not None:
        candidates.append((nslc_limit_mps, self.SOURCE_NAV))
      if car_limit_mps is not None:
        candidates.append((car_limit_mps, self.SOURCE_CAR))
      # car_fallback (5): only use car if no map/nav data
      if self._policy == 5 and not candidates and car_limit_mps is not None:
        candidates.append((car_limit_mps, self.SOURCE_CAR))
      if candidates:
        raw_limit, source = min(candidates, key=lambda x: x[0])
    else:
      raw_limit = None

    if raw_limit is None or raw_limit <= 0:
      return ResolvedLimit(
        source=self.SOURCE_NONE,
        limit_mps=0.0,
        distance_to_change_m=distance_to_change_m,
        active=False,
      )

    return ResolvedLimit(
      source=source,
      limit_mps=raw_limit,
      distance_to_change_m=distance_to_change_m,
      active=True,
    )

  def apply_to_v_cruise(self, v_cruise: float, resolved: ResolvedLimit) -> float:
    if not resolved.active:
      return v_cruise
    if resolved.limit_mps < v_cruise:
      return resolved.limit_mps
    return v_cruise
