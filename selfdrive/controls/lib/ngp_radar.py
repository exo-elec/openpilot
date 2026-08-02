"""Normalized Tesla-gateway radar2D/radar3D tracking and zone assessment."""

from dataclasses import dataclass, replace
from enum import IntEnum
from math import inf


class RadarSource(IntEnum):
  RADAR_2D = 0
  RADAR_3D = 1
  CAMERA = 2
  VEHICLE_BSM = 3


@dataclass(frozen=True)
class RadarObservation:
  track_id: int
  d_rel: float
  y_rel: float
  v_rel: float
  probability: float
  source: RadarSource


@dataclass(frozen=True)
class RadarTrack(RadarObservation):
  timestamp: float
  age: int = 1


@dataclass(frozen=True)
class RadarZones:
  left_detected: bool
  right_detected: bool
  rear_detected: bool
  lca_blocked_left: bool
  lca_blocked_right: bool
  left_ttc: float = inf
  right_ttc: float = inf


class NGPRadarTracker:
  TRACK_TIMEOUT = 0.6
  MIN_PROBABILITY = 0.35

  def __init__(self, smoothing: float = 0.45):
    self.smoothing = max(0.0, min(1.0, float(smoothing)))
    self._tracks: dict[tuple[RadarSource, int], RadarTrack] = {}

  def update(self, observations, timestamp: float) -> tuple[RadarTrack, ...]:
    for obs in observations or ():
      if obs.probability < self.MIN_PROBABILITY:
        continue
      key = (obs.source, int(obs.track_id))
      previous = self._tracks.get(key)
      if previous is None:
        track = RadarTrack(**obs.__dict__, timestamp=float(timestamp))
      else:
        a = self.smoothing
        track = RadarTrack(
          track_id=obs.track_id,
          d_rel=a * obs.d_rel + (1.0 - a) * previous.d_rel,
          y_rel=a * obs.y_rel + (1.0 - a) * previous.y_rel,
          v_rel=a * obs.v_rel + (1.0 - a) * previous.v_rel,
          probability=max(obs.probability, previous.probability * 0.9),
          source=obs.source,
          timestamp=float(timestamp),
          age=previous.age + 1,
        )
      self._tracks[key] = track
    self._tracks = {key: track for key, track in self._tracks.items()
                    if float(timestamp) - track.timestamp <= self.TRACK_TIMEOUT}
    return tuple(sorted(self._tracks.values(), key=lambda track: (track.d_rel, track.track_id)))

  @staticmethod
  def _ttc(track: RadarTrack) -> float:
    if track.d_rel < 0.0 and track.v_rel > 0.1:
      return -track.d_rel / track.v_rel
    if track.d_rel > 0.0 and track.v_rel < -0.1:
      return track.d_rel / -track.v_rel
    return inf

  @classmethod
  def assess_zones(cls, tracks) -> RadarZones:
    tracks = tuple(tracks or ())
    left = tuple(t for t in tracks if 1.2 <= t.y_rel <= 6.0 and -100.0 <= t.d_rel <= 30.0)
    right = tuple(t for t in tracks if -6.0 <= t.y_rel <= -1.2 and -100.0 <= t.d_rel <= 30.0)
    rear = tuple(t for t in tracks if -30.0 <= t.d_rel <= -3.0 and abs(t.y_rel) <= 6.0)
    left_ttc = min((cls._ttc(t) for t in left), default=inf)
    right_ttc = min((cls._ttc(t) for t in right), default=inf)
    left_immediate = any(-15.0 <= t.d_rel <= 8.0 and 1.5 <= t.y_rel <= 5.0 for t in left)
    right_immediate = any(-15.0 <= t.d_rel <= 8.0 and -5.0 <= t.y_rel <= -1.5 for t in right)
    return RadarZones(
      left_detected=bool(left), right_detected=bool(right), rear_detected=bool(rear),
      lca_blocked_left=left_immediate or left_ttc < 5.0,
      lca_blocked_right=right_immediate or right_ttc < 5.0,
      left_ttc=left_ttc, right_ttc=right_ttc,
    )

  @staticmethod
  def with_vehicle_bsm(zones: RadarZones, left: bool, right: bool) -> RadarZones:
    return replace(
      zones,
      left_detected=zones.left_detected or left,
      right_detected=zones.right_detected or right,
      lca_blocked_left=zones.lca_blocked_left or left,
      lca_blocked_right=zones.lca_blocked_right or right,
    )
