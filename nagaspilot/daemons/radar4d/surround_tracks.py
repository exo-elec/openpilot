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
    result.append(dict(trackId=encode_corner_track_id(obj.corner, obj.trackId),
                       rangM=math.hypot(x, y), azimuth=math.degrees(math.atan2(y, x)),
                       elevation=float(obj.elevationDeg), vRel=float(obj.vRel),
                       aRel=math.nan, snrDb=float(obj.snrDb), existenceProb=0.0,
                       pointCount=0, corner=int(obj.corner), source=1))
  return result


def tracked_obstacles(objects):
  result = []
  for obj in objects:
    if not 0 < obj.rangM <= 30:
      continue
    azimuth = math.radians(obj.azimuth)
    result.append(dict(dRel=obj.rangM * math.cos(azimuth), yRel=obj.rangM * math.sin(azimuth),
                       vRel=float(obj.vRel), aRel=float(obj.aRel), trackId=int(obj.trackId),
                       confidence=0.5, prob=0.5, obstacleType=0, dynProp=int(obj.dynProp),
                       length=float(obj.lengthM), width=float(obj.widthM), ttcS=math.nan, ttcValid=False))
  return result
