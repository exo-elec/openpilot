"""NSLC - Navigation Speed Limit Controller (the posted limit the navigation route gives, per-range offsets, confirmation).

Moved from EOP10's `selfdrive/controls/lib/nslc.py`, logic unchanged for the navigation source (its map branch was never fed:
the planner passed `map_data=None`). The offsets and the confirmation are MSLC's (`SLCOffset1..7`, `SLCConfirmLower/Higher`):
one setting for every speed-limit source. Configuration is passed in. Pure: no cereal, no Params.
"""
from __future__ import annotations

from exopilot.selfdrive.controls.lib.mslc import SpeedLimitConfirmation, SpeedLimitOffset


class NSLC:
  def __init__(self, offsets_kph=None, confirm_lower: bool = False, confirm_higher: bool = False):
    self._offset = SpeedLimitOffset(offsets_kph)
    self._confirmation = SpeedLimitConfirmation()
    self._confirmation.configure(confirm_lower, confirm_higher)

  def configure(self, offsets_kph, confirm_lower: bool, confirm_higher: bool) -> None:
    self._offset.set_offsets_kph(offsets_kph)
    self._confirmation.configure(confirm_lower, confirm_higher)

  def update(self, nav_limit_ms: float, driver_overriding: bool, t: float) -> float | None:
    """`nav_limit_ms` is navInstruction.speedLimit (m/s, 0 = none). Returns the limit to cap at, or None."""
    if not nav_limit_ms > 0:
      return None
    limit_ms = nav_limit_ms + self._offset.get_offset_ms(nav_limit_ms)
    return self._confirmation.update(limit_ms, driver_overriding, t)
