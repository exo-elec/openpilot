#!/usr/bin/env python3
"""SteamD geofence enforcement.

Rejects remote control commands when the vehicle is outside a configurable
GPS polygon boundary. Uses the ray-casting (even-odd) point-in-polygon test.

Polygon format (param `SteamDGeofencePolygon`): JSON array of [lat, lon] pairs.
Example: [[37.7749,-122.4194],[37.7755,-122.4180],[37.7735,-122.4175]]

An empty or invalid polygon disables the geofence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from openpilot.common.swaglog import cloudlog


@dataclass(frozen=True)
class LatLon:
  lat: float
  lon: float


class Geofence:
  """Configurable GPS polygon geofence."""

  def __init__(self, polygon_json: str = ""):
    self._polygon: list[LatLon] = []
    self._enabled = False
    if polygon_json:
      self._parse_polygon(polygon_json)

  def _parse_polygon(self, raw: str):
    try:
      arr = json.loads(raw)
      if not isinstance(arr, list) or len(arr) < 3:
        cloudlog.warning("SteamD geofence: polygon needs at least 3 vertices")
        return
      self._polygon = [LatLon(float(p[0]), float(p[1])) for p in arr]
      self._enabled = True
      cloudlog.info(f"SteamD geofence: loaded {len(self._polygon)} vertices")
    except Exception as e:
      cloudlog.warning(f"SteamD geofence: failed to parse polygon: {e}")

  @property
  def enabled(self) -> bool:
    return self._enabled

  def contains(self, lat: float, lon: float) -> bool:
    """Ray-casting point-in-polygon test."""
    if not self._enabled:
      return True

    inside = False
    n = len(self._polygon)
    j = n - 1

    for i in range(n):
      pi = self._polygon[i]
      pj = self._polygon[j]

      # Check if the edge straddles the horizontal line at lat
      if ((pi.lat > lat) != (pj.lat > lat)):
        # Compute x coordinate of intersection
        intersect_lon = pj.lon + (lat - pj.lat) * (pi.lon - pj.lon) / (pi.lat - pj.lat)
        if lon < intersect_lon:
          inside = not inside

      j = i

    return inside

  def check_position(self, lat: float | None, lon: float | None) -> tuple[bool, str | None]:
    """Check if position is inside geofence.

    Returns:
      (allowed, reason)
      allowed — True if inside geofence or geofence disabled
      reason  — None if allowed, else breason string
    """
    if not self._enabled:
      return True, None
    if lat is None or lon is None:
      return False, "No GPS fix — geofence cannot verify position"
    if self.contains(lat, lon):
      return True, None
    return False, f"Outside geofence ({lat:.6f}, {lon:.6f})"
