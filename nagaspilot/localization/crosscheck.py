"""Cross-check the phone's GNSS fix against the comma's, and give one position with an honest accuracy.

The phone's receiver is the more precise one (current phones track two bands, L1 and L5; the comma's receiver is single-band), so the phone's fix leads and the
comma's checks it. Both fixes are first brought to the same instant (the phone's arrives late over the cable) and to the same place (the phone sits to the left or
right of the comma, `lever_arm`). They are then compared against what each says its own error is:

- they agree (within a few combined sigmas): the two are combined by inverse variance, which leans on whichever is tighter (normally the phone) and is tighter than either;
- they disagree: one of them is wrong. If the previous position and the car's motion say which, that one wins; with no prior the phone wins but the position is marked degraded;
- only one is fresh: that one is used, marked degraded;
- neither: no position.

The accuracies are the receivers' own claims; this module trusts them and does not check them against ground truth. Standard library only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from nagaspilot.localization import lever_arm

FRESH_S = 2.0               # a fix older than this (at the target time) is not used
GATE_SIGMAS = 3.0           # how many combined sigmas two fixes may differ and still agree
GATE_FLOOR_M = 1.5          # a floor under the gate, for receivers that claim to be better than they are
MIN_SIGMA_M = 0.5           # a claimed accuracy below this is not believed
MAX_SPEED_DEAD_RECKON = 80.0   # m/s: a larger speed in a fix is bad data, not motion

AGREE = 'agree'
PHONE_ONLY = 'phone_only'
COMMA_ONLY = 'comma_only'
DISAGREE_PHONE = 'disagree_phone'   # they differ; the phone's fix was kept
DISAGREE_COMMA = 'disagree_comma'   # they differ; the comma's fix was kept
NONE = 'none'


@dataclass(frozen=True)
class Fix:
  t: float                 # seconds, on the comma's clock (the phone's time is converted when the message is received)
  lat: float
  lon: float
  h_acc_m: float           # the receiver's claimed horizontal accuracy, one sigma
  speed_mps: float = 0.0
  bearing_deg: float = 0.0


@dataclass(frozen=True)
class Position:
  state: str
  lat: float | None = None
  lon: float | None = None
  h_acc_m: float | None = None
  separation_m: float | None = None    # between the two aligned fixes, when both were fresh
  degraded: bool = True
  source: str = 'none'                 # 'phone', 'comma' or 'both'


def advance(fix: Fix, t: float) -> Fix:
  """The fix carried to time t by its own speed and bearing (a short dead reckoning, a second or two at most)."""
  dt = t - fix.t
  if dt == 0 or not 0 <= fix.speed_mps <= MAX_SPEED_DEAD_RECKON:
    return fix
  h = math.radians(fix.bearing_deg)
  m_lat, m_lon = lever_arm.metres_per_degree(fix.lat)
  d = fix.speed_mps * dt
  return Fix(t, fix.lat + d * math.cos(h) / m_lat, fix.lon + d * math.sin(h) / m_lon,
             math.hypot(fix.h_acc_m, 0.1 * abs(d)), fix.speed_mps, fix.bearing_deg)   # the guess grows less certain with the distance carried


def _sigma(fix: Fix) -> float:
  return max(fix.h_acc_m, MIN_SIGMA_M)


def combine(comma: Fix | None, phone: Fix | None, now: float, side: str, offset_m: float = lever_arm.DEFAULT_OFFSET_M,
            prior: Position | None = None) -> Position:
  """One position at time `now` from the comma's and the phone's latest fixes (either may be None)."""
  comma_ok = comma is not None and abs(now - comma.t) <= FRESH_S
  phone_ok = phone is not None and abs(now - phone.t) <= FRESH_S
  c = advance(comma, now) if comma_ok else None
  p = None
  if phone_ok:
    p = advance(phone, now)
    lat, lon = lever_arm.phone_to_comma(p.lat, p.lon, p.bearing_deg, side, offset_m)
    p = Fix(p.t, lat, lon, p.h_acc_m, p.speed_mps, p.bearing_deg)

  if c is None and p is None:
    return Position(NONE)
  if p is None:
    return Position(COMMA_ONLY, c.lat, c.lon, _sigma(c), None, True, 'comma')
  if c is None:
    return Position(PHONE_ONLY, p.lat, p.lon, _sigma(p), None, True, 'phone')

  sep = lever_arm.distance_m(c.lat, c.lon, p.lat, p.lon)
  sc, sp = _sigma(c), _sigma(p)
  gate = max(GATE_SIGMAS * math.hypot(sc, sp), GATE_FLOOR_M)
  if sep <= gate:
    wc, wp = 1.0 / (sc * sc), 1.0 / (sp * sp)
    lat = (c.lat * wc + p.lat * wp) / (wc + wp)
    lon = (c.lon * wc + p.lon * wp) / (wc + wp)
    return Position(AGREE, lat, lon, 1.0 / math.sqrt(wc + wp), sep, False, 'both')

  # they disagree: the previous position, carried forward, says which one to believe
  if prior is not None and prior.lat is not None:
    dc = lever_arm.distance_m(prior.lat, prior.lon, c.lat, c.lon)
    dp = lever_arm.distance_m(prior.lat, prior.lon, p.lat, p.lon)
    if dc < dp:
      return Position(DISAGREE_COMMA, c.lat, c.lon, sc, sep, True, 'comma')
  return Position(DISAGREE_PHONE, p.lat, p.lon, sp, sep, True, 'phone')
