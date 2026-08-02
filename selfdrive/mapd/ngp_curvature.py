"""Convert an already-available route polyline into optional MTSC curves."""

from dataclasses import dataclass
from math import cos, hypot, pi


EARTH_RADIUS_M = 6371000.0


@dataclass(frozen=True)
class RoutePoint:
  latitude: float
  longitude: float


def _local_xy(origin: RoutePoint, point: RoutePoint):
  lat_scale = pi / 180.0 * EARTH_RADIUS_M
  lon_scale = lat_scale * cos(origin.latitude * pi / 180.0)
  return ((point.longitude - origin.longitude) * lon_scale,
          (point.latitude - origin.latitude) * lat_scale)


def route_curvatures(points) -> tuple[tuple[float, float], ...]:
  """Return ``(distance_m, curvature_1_per_m)`` for a route polyline."""
  points = tuple(points or ())
  if len(points) < 3:
    return ()
  origin = points[0]
  xy = tuple(_local_xy(origin, point) for point in points)
  cumulative = [0.0]
  for first, second in zip(xy, xy[1:], strict=False):
    cumulative.append(cumulative[-1] + hypot(second[0] - first[0], second[1] - first[1]))

  curves = []
  for index in range(1, len(xy) - 1):
    a = hypot(xy[index][0] - xy[index - 1][0], xy[index][1] - xy[index - 1][1])
    b = hypot(xy[index + 1][0] - xy[index][0], xy[index + 1][1] - xy[index][1])
    c = hypot(xy[index + 1][0] - xy[index - 1][0], xy[index + 1][1] - xy[index - 1][1])
    cross = abs((xy[index][0] - xy[index - 1][0]) * (xy[index + 1][1] - xy[index - 1][1])
                - (xy[index][1] - xy[index - 1][1]) * (xy[index + 1][0] - xy[index - 1][0]))
    curvature = 2.0 * cross / (a * b * c) if min(a, b, c) > 0.1 else 0.0
    if curvature > 0.001:
      curves.append((cumulative[index], min(0.1, curvature)))
  return tuple(curves)
