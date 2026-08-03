"""Advisory collision-risk assessment using normalized radar tracks."""

from dataclasses import dataclass
from enum import IntEnum
from math import inf


class CollisionLevel(IntEnum):
  NONE = 0
  CAUTION = 1
  WARNING = 2
  CRITICAL = 3


@dataclass(frozen=True)
class CollisionResult:
  level: CollisionLevel
  track_id: int | None
  ttc: float
  distance: float
  safe_distance: float
  decel_suggestion: float
  control_authority: bool = False


class NGPCollisionRisk:
  RESPONSE_TIME = 1.0
  EGO_BRAKING = 4.0
  OBJECT_BRAKING = 4.0
  PATH_HALF_WIDTH = 1.75

  @classmethod
  def _safe_distance(cls, v_ego: float, v_object: float) -> float:
    response = v_ego * cls.RESPONSE_TIME
    ego_stop = v_ego * v_ego / (2.0 * cls.EGO_BRAKING)
    object_stop = v_object * v_object / (2.0 * cls.OBJECT_BRAKING)
    return max(2.0, response + ego_stop - object_stop)

  def evaluate(self, v_ego: float, tracks) -> CollisionResult:
    best = None
    for track in tracks or ():
      if track.d_rel <= 0.0 or abs(track.y_rel) > self.PATH_HALF_WIDTH:
        continue
      closing_speed = max(0.0, -track.v_rel)
      ttc = track.d_rel / closing_speed if closing_speed > 0.1 else inf
      v_object = max(0.0, float(v_ego) + track.v_rel)
      safe_distance = self._safe_distance(max(0.0, v_ego), v_object)
      score = min(ttc / 4.0, track.d_rel / safe_distance)
      candidate = (score, track, ttc, safe_distance)
      if best is None or candidate[0] < best[0]:
        best = candidate
    if best is None:
      return CollisionResult(CollisionLevel.NONE, None, inf, inf, 0.0, 0.0)

    _, track, ttc, safe_distance = best
    if ttc < 1.5:
      level, decel = CollisionLevel.CRITICAL, -3.0
    elif ttc < 2.5 or track.d_rel < safe_distance * 0.65:
      level, decel = CollisionLevel.WARNING, -2.0
    elif ttc < 4.0 or track.d_rel < safe_distance:
      level, decel = CollisionLevel.CAUTION, -1.0
    else:
      level, decel = CollisionLevel.NONE, 0.0
    return CollisionResult(level, track.track_id, ttc, track.d_rel, safe_distance, decel)
