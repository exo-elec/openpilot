#!/usr/bin/env python3
"""
NSLC - Navigation Speed Limit Controller

Handles speed limits from navigation sources with FrogPilot enhancements:
  - Per-speed-range offsets (bucket-based)
  - Driver confirmation for limit changes
"""

from __future__ import annotations

import time

from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.eop_utils import SpeedLimitOffset, SpeedLimitConfirmation


class NSLC:
  PARAM_REFRESH_S = 2.0

  def __init__(self):
    self._params = Params()
    self._enabled = self._params.get_bool("EOPNSLCEnabled")
    self._last_param_t = 0.0

    self._offset = SpeedLimitOffset(self._params)
    self._confirmation = SpeedLimitConfirmation()

  def update(
    self,
    nav_instruction,
    map_data,
    v_ego: float,
    driver_overriding: bool,
    current_time: float
  ) -> tuple[float | None, str]:
    now = time.monotonic()
    if now - self._last_param_t >= self.PARAM_REFRESH_S:
      self._last_param_t = now
      self._enabled = self._params.get_bool("EOPNSLCEnabled")
      self._offset.refresh(now)
      self._confirmation.refresh_params(self._params, now)

    if not self._enabled:
      return None, "disabled"

    # Prefer OSM mapData, fallback to navInstruction
    raw_limit = None
    source_is_map = False
    if map_data is not None and map_data.speedLimit > 0:
      raw_limit = map_data.speedLimit
      source_is_map = True
    elif nav_instruction is not None and nav_instruction.speedLimit > 0:
      raw_limit = nav_instruction.speedLimit
      source_is_map = False

    if raw_limit is None:
      return None, "unavailable"

    # mapData.speedLimit is in km/h; navInstruction.speedLimit is in m/s
    if source_is_map:
      limit_ms = raw_limit * CV.KPH_TO_MS
    else:
      limit_ms = raw_limit

    # Apply per-speed-range offset
    limit_ms += self._offset.get_offset_ms(limit_ms)

    # Apply confirmation logic
    confirmed_ms = self._confirmation.update(limit_ms, driver_overriding, current_time)
    if confirmed_ms is None:
      return None, "unavailable"

    return confirmed_ms, "active"


def get_nslc_speed(
  nav_instruction,
  map_data,
  v_ego: float,
  driver_overriding: bool = False
) -> float | None:
  nslc = NSLC()
  speed, _ = nslc.update(
    nav_instruction=nav_instruction,
    map_data=map_data,
    v_ego=v_ego,
    driver_overriding=driver_overriding,
    current_time=time.monotonic()
  )
  return speed
