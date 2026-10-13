"""The phone's GNSS fix message (NCP 0x0620, PHONE -> DEVICE, JSON), turned into a `crosscheck.Fix` on the comma's clock.

The phone stamps its fix with its own wall clock (`t_ms`). The comma cannot compare that with its own clock directly: the two differ by an unknown offset, plus the link delay. `ClockOffset`
estimates phone-clock-to-comma-clock from the fixes themselves: for each message, (arrival time on the comma) - (phone's stamp) is the offset plus a non-negative delay, so the smallest
value seen in a recent window is the best estimate of the offset (the least-delayed message). A fix is then stamped `phone time + offset`: at most the link delay too early, never in the future.
Standard library only; the wire fields are the contract with NavPilot's `makeGnssFixMessage`.
"""
from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass

from nagaspilot.localization import lever_arm
from nagaspilot.localization.crosscheck import Fix

WINDOW = 120            # messages kept for the offset estimate (about two minutes at 1 Hz)
MAX_H_ACC_M = 500.0     # a fix that admits worse than this is not a position
MAX_SPEED_MPS = 120.0


class BadFix(ValueError):
  pass


@dataclass(frozen=True)
class Mount:
  side: str
  offset_m: float


class ClockOffset:
  def __init__(self, window: int = WINDOW):
    self._samples: deque[float] = deque(maxlen=window)

  def add(self, phone_s: float, arrival_s: float) -> None:
    self._samples.append(arrival_s - phone_s)

  @property
  def offset(self) -> float | None:
    return min(self._samples) if self._samples else None


def parse(payload: bytes | str, arrival_s: float, clock: ClockOffset) -> tuple[Fix, Mount]:
  """Validate one message; return the fix on the comma's clock and the phone's mount. Raises BadFix."""
  try:
    d = json.loads(payload)
    lat, lon = float(d['lat']), float(d['lon'])
    acc = float(d['h_acc'])
    t_phone = float(d['t_ms']) / 1000.0
  except (ValueError, KeyError, TypeError) as e:
    raise BadFix(f'unreadable GNSS fix: {e}') from e
  if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or not all(math.isfinite(v) for v in (lat, lon, acc, t_phone)):
    raise BadFix('position out of range')
  if not 0.0 <= acc <= MAX_H_ACC_M:
    raise BadFix(f'accuracy {acc} m is not usable')
  speed = d.get('speed')
  bearing = d.get('bearing')
  speed = float(speed) if speed is not None else 0.0
  if not 0.0 <= speed <= MAX_SPEED_MPS:
    raise BadFix(f'speed {speed} m/s is not usable')
  bearing = float(bearing) % 360.0 if bearing is not None else 0.0
  mount = d.get('mount') or {}
  side = mount.get('side', lever_arm.LEFT)
  off = float(mount.get('offset_m', lever_arm.DEFAULT_OFFSET_M))
  if side not in (lever_arm.LEFT, lever_arm.RIGHT) or not 0.0 <= off <= lever_arm.MAX_OFFSET_M:
    raise BadFix('bad mount')
  clock.add(t_phone, arrival_s)
  return Fix(t_phone + clock.offset, lat, lon, acc, speed, bearing), Mount(side, off)
