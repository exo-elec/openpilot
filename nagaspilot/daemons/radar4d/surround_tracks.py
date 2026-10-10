"""BLE 3D tracks -> canonical surround objects and a planar BSD view."""
import math
from nagaspilot.controls.ngp_radar2d import ground_range
from openpilot.selfdrive.controls.radar_corner_geometry import corner_local_to_vehicle_frame, encode_corner_track_id


def surround_tracks(objects, poses):
  result = []
  for obj in objects:
    horizontal = ground_range(float(obj.rangM), float(obj.elevationDeg))
    pose = (poses or {}).get(obj.corner)
    if pose is None or not math.isfinite(horizontal) or horizontal <= 0 or not math.isfinite(obj.azimuthDeg):
      continue
    x, y = corner_local_to_vehicle_frame(horizontal, obj.azimuthDeg, pose)
    # Current confirmed poses provide planar mounting geometry. Height remains
    # measured relative to the leveled sensor plane; do not invent mount height.
    z = float(obj.rangM) * math.sin(math.radians(obj.elevationDeg))
    planar_range = math.hypot(x, y)
    result.append(dict(trackId=encode_corner_track_id(obj.corner, obj.trackId),
                       rangM=math.hypot(planar_range, z), azimuth=math.degrees(math.atan2(y, x)),
                       elevation=math.degrees(math.atan2(z, planar_range)), vRel=float(obj.vRel),
                       aRel=math.nan, snrDb=float(obj.snrDb), existenceProb=0.0,
                       pointCount=0, corner=int(obj.corner), source=1,
                       sensorRangeM=float(obj.rangM), sensorElevationDeg=float(obj.elevationDeg)))
  return result


def tracked_obstacles(objects):
  result = []
  for obj in objects:
    horizontal = ground_range(float(obj.rangM), float(getattr(obj, 'elevation', 0.0)))
    if not 0 < horizontal <= 30:
      continue
    azimuth = math.radians(obj.azimuth)
    result.append(dict(dRel=horizontal * math.cos(azimuth), yRel=horizontal * math.sin(azimuth),
                       vRel=float(obj.vRel), aRel=float(obj.aRel), trackId=int(obj.trackId),
                       confidence=0.5, prob=0.5, obstacleType=0, dynProp=int(obj.dynProp),
                       length=float(obj.lengthM), width=float(obj.widthM), ttcS=math.nan, ttcValid=False))
  return result
