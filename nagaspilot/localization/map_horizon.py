"""The phone's map horizon: the next 500 m to 1 km of the route, shared to the comma and kept fresh as the car moves.

The HD map (later from a server) lives in the phone app. The comma has OpenStreetMap (mapd, osm_localizer) and its own position, so it can always say where it is; what it
lacks is the lane-level map and the turn commands. The phone sends a rolling window ahead, `MAP_HORIZON` (NCP 0x0621, JSON): a centreline, the lanes along it, the
maneuvers with the lanes to be in, and speed limits, all indexed by arc length `s` along the route. The comma places itself on the window (nearest point on the centreline),
follows the next maneuver, and asks for more before the window runs out: whenever less than `min_ahead_m` (500 m) remains and the route is not at its end.

Everything here is pure and works on the comma's clock. The window is advice: it never overrides the comma's own position or the driver. A missing, old or off-route window
reads as no guidance and the comma falls back to its OSM navigation. Standard library only.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from nagaspilot.localization import lever_arm

MIN_LENGTH_M, MAX_LENGTH_M = 200.0, 5000.0
MAX_POINTS = 600
MAX_GAP_M = 120.0              # consecutive centreline points further apart than this are not a centreline
LENGTH_TOLERANCE = 0.05        # the declared length may differ from the polyline's by this fraction (and 10 m)
MIN_AHEAD_M = 500.0            # ask for more when less than this remains
MAX_CROSS_TRACK_M = 30.0       # further from the centreline than this is off the route
DEFAULT_TTL_S = 30.0


class BadHorizon(ValueError):
  pass


@dataclass(frozen=True)
class Maneuver:
  s: float
  type: str
  lane_hint: tuple[int, ...] = ()
  road: str = ''
  speed_limit_kph: float | None = None


@dataclass(frozen=True)
class MapHorizon:
  horizon_id: int
  route_id: str
  map_version: str
  source: str
  start_s: float
  length_m: float
  points: tuple[tuple[float, float], ...]
  arc: tuple[float, ...]                      # absolute arc length of each point (start_s at the first)
  lanes: tuple[tuple[float, int, tuple[int, ...]], ...]    # (s, lane count, recommended lanes), sorted by s
  maneuvers: tuple[Maneuver, ...]
  speed_limits: tuple[tuple[float, float], ...]            # (s, kph)
  ttl_s: float
  end_of_route: bool
  received_at: float = 0.0

  @property
  def end_s(self) -> float:
    return self.start_s + self.length_m

  @classmethod
  def parse(cls, payload: bytes | str, received_at: float) -> MapHorizon:
    try:
      d = json.loads(payload)
      pts = tuple((float(p[0]), float(p[1])) for p in d['polyline'])
      start_s = float(d['start_s'])
      length_m = float(d['length_m'])
      hid = int(d['horizon_id'])
      route_id = str(d['route_id'])
    except (ValueError, KeyError, TypeError, IndexError) as e:
      raise BadHorizon(f'unreadable horizon: {e}') from e
    if not MIN_LENGTH_M <= length_m <= MAX_LENGTH_M:
      raise BadHorizon(f'length {length_m} m outside {MIN_LENGTH_M:g}..{MAX_LENGTH_M:g}')
    if not 2 <= len(pts) <= MAX_POINTS:
      raise BadHorizon(f'{len(pts)} points')
    if not all(-90 <= la <= 90 and -180 <= lo <= 180 and math.isfinite(la) and math.isfinite(lo) for la, lo in pts):
      raise BadHorizon('a point is out of range')
    arc = [start_s]
    for a, b in zip(pts, pts[1:], strict=False):
      gap = lever_arm.distance_m(a[0], a[1], b[0], b[1])
      if gap > MAX_GAP_M:
        raise BadHorizon(f'a gap of {gap:.0f} m in the centreline')
      arc.append(arc[-1] + gap)
    if abs((arc[-1] - start_s) - length_m) > max(10.0, LENGTH_TOLERANCE * length_m):
      raise BadHorizon('the declared length does not match the centreline')
    try:
      maneuvers = tuple(sorted((Maneuver(float(m['s']), str(m['type']), tuple(int(x) for x in m.get('lane_hint', ())), str(m.get('road', '')),
                                         float(m['speed_limit_kph']) if m.get('speed_limit_kph') is not None else None)
                                for m in d.get('maneuvers', [])), key=lambda m: m.s))
      lanes = tuple(sorted(((float(x['s']), int(x['count']), tuple(int(v) for v in x.get('recommended', ()))) for x in d.get('lanes', [])), key=lambda t: t[0]))
      limits = tuple(sorted(((float(x['s']), float(x['kph'])) for x in d.get('speed_limits', [])), key=lambda t: t[0]))
    except (ValueError, KeyError, TypeError) as e:
      raise BadHorizon(f'bad maneuver, lane or limit: {e}') from e
    for m in maneuvers:
      if not start_s - 1.0 <= m.s <= start_s + length_m + 1.0:
        raise BadHorizon(f'a maneuver at s={m.s} is outside the window')
    return cls(hid, route_id, str(d.get('map_version', '')), str(d.get('source', '')), start_s, length_m, pts, tuple(arc), lanes, maneuvers, limits,
               float(d.get('ttl_s', DEFAULT_TTL_S)), bool(d.get('end_of_route', False)), received_at)

  def locate(self, lat: float, lon: float, near_s: float | None = None) -> tuple[float, float]:
    """(s, cross-track metres): the nearest point on the centreline. With `near_s` only segments within 300 m of it are tried (a loop-back road must not snap to its far side)."""
    m_lat, m_lon = lever_arm.metres_per_degree(lat)
    best = (self.start_s, float('inf'))
    for i in range(len(self.points) - 1):
      if near_s is not None and not (self.arc[i] - 300.0 <= near_s <= self.arc[i + 1] + 300.0):
        continue
      (la1, lo1), (la2, lo2) = self.points[i], self.points[i + 1]
      ax, ay = (lo1 - lon) * m_lon, (la1 - lat) * m_lat
      bx, by = (lo2 - lon) * m_lon, (la2 - lat) * m_lat
      dx, dy = bx - ax, by - ay
      seg2 = dx * dx + dy * dy
      t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
      px, py = ax + t * dx, ay + t * dy
      dist = math.hypot(px, py)
      if dist < best[1]:
        best = (self.arc[i] + t * (self.arc[i + 1] - self.arc[i]), dist)
    return best

  def lane_at(self, s: float) -> tuple[int | None, tuple[int, ...]]:
    count, rec = None, ()
    for ls, c, r in self.lanes:
      if ls <= s:
        count, rec = c, r
      else:
        break
    return count, rec

  def limit_at(self, s: float) -> float | None:
    kph = None
    for ls, k in self.speed_limits:
      if ls <= s:
        kph = k
      else:
        break
    return kph


OK, REFRESH, OFF_ROUTE, STALE, NONE = 'ok', 'refresh', 'off_route', 'stale', 'none'


@dataclass(frozen=True)
class Guidance:
  state: str
  s: float | None = None
  remaining_m: float | None = None
  cross_track_m: float | None = None
  next_maneuver: Maneuver | None = None
  distance_to_maneuver_m: float | None = None
  lane_count: int | None = None
  recommended_lanes: tuple[int, ...] = ()
  speed_limit_kph: float | None = None
  wants_refresh: bool = False


@dataclass
class HorizonTracker:
  min_ahead_m: float = MIN_AHEAD_M
  max_cross_track_m: float = MAX_CROSS_TRACK_M
  current: MapHorizon | None = None
  last_s: float | None = field(default=None, repr=False)

  def offer(self, h: MapHorizon) -> bool:
    """Take a new horizon if it is newer (or for a new route). An older or repeated one is ignored."""
    cur = self.current
    if cur is not None and h.route_id == cur.route_id and h.horizon_id <= cur.horizon_id:
      return False
    if cur is None or h.route_id != cur.route_id:
      self.last_s = None
    self.current = h
    return True

  def guidance(self, lat: float, lon: float, now: float) -> Guidance:
    h = self.current
    if h is None:
      return Guidance(NONE)
    if now - h.received_at > h.ttl_s:
      return Guidance(STALE, wants_refresh=True)
    s, xt = h.locate(lat, lon, self.last_s)
    if xt > self.max_cross_track_m and self.last_s is not None:
      s, xt = h.locate(lat, lon)               # after a gap in fixes the car can be further on than the search window: look everywhere before giving up
    if xt > self.max_cross_track_m:
      return Guidance(OFF_ROUTE, s, h.end_s - s, xt, wants_refresh=True)       # the caller debounces; the phone should reroute
    self.last_s = s if self.last_s is None else max(self.last_s, s)          # the position along the route never runs backwards
    s = self.last_s
    remaining = h.end_s - s
    nxt = next((m for m in h.maneuvers if m.s >= s - 1.0), None)
    count, rec = h.lane_at(s)
    wants = remaining < self.min_ahead_m and not h.end_of_route
    return Guidance(REFRESH if wants else OK, s, remaining, xt, nxt, (nxt.s - s) if nxt else None, count, rec, h.limit_at(s), wants)
