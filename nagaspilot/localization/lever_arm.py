"""Where the phone's GNSS fix is, relative to the car's reference point.

The comma is always at the centre of the car (on the windshield), so it is the reference point; the phone is mounted to its left or right. A phone fix is
therefore a small sideways step away from where the comma would read. To cross-check the two receivers (and later to use the phone's fix for lane-level
position, where a few tenths of a metre matter) the phone's fix is moved to the comma's position along the car's heading. The offset is only sideways: the two
mounts are at about the same distance along the car. Standard library only.

Heading is degrees clockwise from true north (a GNSS bearing). Positions are WGS84 degrees; at these distances (well under a metre) a local flat step is exact enough.
"""
from __future__ import annotations

import math

LEFT = 'left'
RIGHT = 'right'

# Metres per degree of latitude at the equator and its change with latitude (WGS84 meridian arc, series to the second term).
_M_PER_DEG_LAT_0 = 111132.92
_M_PER_DEG_LAT_2 = -559.82
_M_PER_DEG_LON_0 = 111412.84
_M_PER_DEG_LON_2 = -93.5

DEFAULT_OFFSET_M = 0.20       # a typical sideways distance between a centre-mounted comma and a phone beside it; set it in the app
MAX_OFFSET_M = 1.0            # more than this is a typing mistake, not a mount


def metres_per_degree(lat_deg: float) -> tuple[float, float]:
  """(metres per degree of latitude, metres per degree of longitude) at lat_deg."""
  phi = math.radians(lat_deg)
  m_lat = _M_PER_DEG_LAT_0 + _M_PER_DEG_LAT_2 * math.cos(2 * phi)
  m_lon = _M_PER_DEG_LON_0 * math.cos(phi) + _M_PER_DEG_LON_2 * math.cos(3 * phi)
  return m_lat, m_lon


def phone_to_comma(lat: float, lon: float, heading_deg: float, side: str, offset_m: float = DEFAULT_OFFSET_M) -> tuple[float, float]:
  """The comma's position given the phone's fix: step `offset_m` across the car, toward the comma.

  `side` is where the phone is relative to the comma, seen from the driver: 'left' means the comma is to the phone's right.
  """
  if side not in (LEFT, RIGHT):
    raise ValueError(f"side must be 'left' or 'right', not {side!r}")
  if not 0.0 <= offset_m <= MAX_OFFSET_M:
    raise ValueError(f'offset of {offset_m} m is outside 0..{MAX_OFFSET_M} m')
  h = math.radians(heading_deg)
  # unit vector pointing to the car's right, in (east, north)
  right_e, right_n = math.cos(h), -math.sin(h)
  sign = 1.0 if side == LEFT else -1.0           # phone on the left: the comma is to the right of it
  de, dn = sign * offset_m * right_e, sign * offset_m * right_n
  m_lat, m_lon = metres_per_degree(lat)
  return lat + dn / m_lat, lon + de / m_lon


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  """Small-distance (flat) separation in metres between two fixes."""
  m_lat, m_lon = metres_per_degree((lat1 + lat2) / 2)
  return math.hypot((lat2 - lat1) * m_lat, (lon2 - lon1) * m_lon)
