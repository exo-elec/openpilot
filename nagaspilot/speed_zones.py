"""Canonical NagasPilot operating zones.

These values classify behavior; they do not impose a vehicle speed limit.
"""

from enum import IntEnum


CITY_SPEED_MPS = 12.0
HIGHWAY_SPEED_MPS = 24.0
MAX_SPEED_MPS = 36.0


class SpeedZone(IntEnum):
  CRAWL = 0
  CITY = 1
  HIGHWAY = 2
  MAXIMUM = 3


def speed_zone(v_ego: float) -> SpeedZone:
  speed = max(0.0, float(v_ego))
  if speed < CITY_SPEED_MPS:
    return SpeedZone.CRAWL
  if speed < HIGHWAY_SPEED_MPS:
    return SpeedZone.CITY
  if speed < MAX_SPEED_MPS:
    return SpeedZone.HIGHWAY
  return SpeedZone.MAXIMUM
