"""
soc.py — Safety lateral nudge when closing speed is high.

When a tracked object ahead is closing fast, the ego path is subtly shifted
sideways to create subjective safety distance without a full lane change.

Design rules:
  - Activates when closing speed > CLOSING_SPEED_THRESHOLD (5 m/s / 18 km/h)
  - Direction: away from the object center; toward the side with more lane space
  - Magnitude: proportional to (closing speed × distance urgency), capped at MAX_OFFSET_M
  - Applied with taper: full offset near the object, fades to zero at 30m+
  - Low-pass filtered for smooth, jerk-free transitions
  - Clamped to stay within lane boundaries (with LANE_MARGIN)
  - Resets to zero when no eligible threat is present
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from openpilot.system.hardware import HARDWARE


# --- Tunable parameters ---

# Minimum closing speed to trigger the offset (m/s). Below this → no action.
# 5 m/s ≈ 18 km/h relative closing speed.
CLOSING_SPEED_THRESHOLD = 5.0

# Maximum lateral offset applied to any trajectory point (meters).
# 0.5m is a subtle nudge — does not feel like a lane change.
MAX_OFFSET_M = 0.5

# Object must be within this lateral band from ego center to be considered.
# Wider than PATH_WIDTH so we react proactively to objects slightly off-center.
LATERAL_TRIGGER_WIDTH_M = 2.5

# Distance window where offset is active.
# Below MIN: emergency braking territory, not lateral offset.
# Above MAX: too far to matter.
MIN_DISTANCE_M = 8.0
MAX_DISTANCE_M = HARDWARE.get_max_reliable_depth_m()  # ExoPilot 01M: 80m

# Trajectory taper: offset fades linearly from full at TAPER_START_M
# to zero at TAPER_END_M.  Points beyond TAPER_END_M get zero offset.
TAPER_START_M = 0.0
TAPER_END_M = 30.0

# Lane boundary margin — never nudge to within this distance of the lane edge.
LANE_MARGIN_M = 0.35

# Low-pass filter coefficient for offset transitions (0 = frozen, 1 = instant).
# 0.12 → ~8-frame time constant at 20 Hz ≈ 0.4 s settle time.
OFFSET_FILTER_ALPHA = 0.12


@dataclass
class OffsetResult:
  offset_m: float         # signed lateral offset at ego (meters; + = right, - = left)
  trigger_distance: float  # distance to the triggering object (m); inf if no trigger
  closing_speed: float     # closing speed that triggered offset (m/s); 0 if no trigger
  score: float             # internal confidence score [0..1]


class SOC:
  """
  Computes a smooth lateral nudge when a fast-closing object is ahead.

  Call `compute()` once per frame from pathd.update().
  Use `offsets_for_trajectory()` to get per-point offsets for enhancedTrajectory.y.
  """

  def __init__(self) -> None:
    self._filtered_offset: float = 0.0

  # ------------------------------------------------------------------
  # Primary interface
  # ------------------------------------------------------------------

  def compute(
    self,
    tracks: list,
    ego_velocity: float,
    lane_left_m: float = -1.8,
    lane_right_m: float = 1.8,
  ) -> OffsetResult:
    """
    Compute the filtered lateral offset for the current frame.

    Args:
      tracks:       List of TrackedCluster (or any object with x/z/vz attributes).
      ego_velocity: Ego forward speed in m/s (from carState.vEgo).
      lane_left_m:  Left lane boundary in meters (negative value).
      lane_right_m: Right lane boundary in meters (positive value).

    Returns:
      OffsetResult with the signed offset and diagnostics.
    """
    result = self._find_worst_threat(tracks, ego_velocity, lane_left_m, lane_right_m)
    target = result.offset_m

    # Clamp to lane boundaries before filtering
    target = _clamp(target, lane_left_m + LANE_MARGIN_M, lane_right_m - LANE_MARGIN_M)

    # Low-pass filter — smooth application
    self._filtered_offset = (
      OFFSET_FILTER_ALPHA * target
      + (1.0 - OFFSET_FILTER_ALPHA) * self._filtered_offset
    )

    return OffsetResult(
      offset_m=self._filtered_offset,
      trigger_distance=result.trigger_distance,
      closing_speed=result.closing_speed,
      score=result.score,
    )

  def offsets_for_trajectory(
    self,
    offset_m: float,
    path_x: list[float],
  ) -> list[float]:
    """
    Build per-point lateral offset list for a trajectory.

    The offset tapers from full at TAPER_START_M to zero at TAPER_END_M,
    so the far end of the trajectory returns to the policy curvature path.

    Args:
      offset_m: The base offset from compute().offset_m.
      path_x:   Forward distances for each trajectory point (meters).

    Returns:
      List of per-point lateral offsets, same length as path_x.
    """
    offsets = []
    taper_range = max(TAPER_END_M - TAPER_START_M, 1.0)
    for x in path_x:
      if x <= TAPER_START_M:
        fade = 1.0
      elif x >= TAPER_END_M:
        fade = 0.0
      else:
        fade = 1.0 - (x - TAPER_START_M) / taper_range
      offsets.append(offset_m * fade)
    return offsets

  def reset(self) -> None:
    """Reset filter state (call on re-initialization or long gaps)."""
    self._filtered_offset = 0.0

  # ------------------------------------------------------------------
  # Internal
  # ------------------------------------------------------------------

  def _find_worst_threat(
    self,
    tracks: list,
    ego_velocity: float,
    lane_left_m: float,
    lane_right_m: float,
  ) -> OffsetResult:
    """
    Scan all tracks and return the worst-scoring threat offset.
    Score = closing_speed_factor × distance_urgency_factor.
    """
    best_score = 0.0
    best_offset = 0.0
    best_distance = float('inf')
    best_closing = 0.0

    for t in tracks:
      # Support both TrackedCluster and dict-like objects
      d = _attr(t, 'dRel', 'z')
      y = _attr(t, 'yRel', 'x')
      vz = _attr(t, 'vRel', 'vz', default=0.0)

      if d is None or y is None:
        continue
      if not (math.isfinite(d) and math.isfinite(y) and math.isfinite(vz)):
        continue

      # Only consider objects ahead in the lateral trigger band
      if d < MIN_DISTANCE_M or d > MAX_DISTANCE_M:
        continue
      if abs(y) > LATERAL_TRIGGER_WIDTH_M:
        continue

      # Closing speed: ego speed minus object forward speed
      # vz is the object's forward velocity in the ego frame:
      #   positive vz → object moving forward (away from ego)
      #   negative or low vz → object is slower or stationary → we're closing
      closing = ego_velocity - vz
      if closing < CLOSING_SPEED_THRESHOLD:
        continue

      # Closing speed factor: normalized over the 5..30 m/s range
      speed_factor = min((closing - CLOSING_SPEED_THRESHOLD) / 25.0, 1.0)

      # Distance urgency: increases as object gets closer to MIN_DISTANCE_M
      dist_factor = 1.0 - (d - MIN_DISTANCE_M) / max(MAX_DISTANCE_M - MIN_DISTANCE_M, 1.0)
      dist_factor = max(0.0, min(dist_factor, 1.0))

      score = speed_factor * dist_factor
      if score <= best_score:
        continue

      best_score = score
      best_distance = d
      best_closing = closing

      # Offset direction: shift away from the object
      if abs(y) < 0.3:
        # Object nearly centered — pick the side with more available lane space
        space_right = lane_right_m
        space_left = -lane_left_m
        raw_dir = 1.0 if space_right >= space_left else -1.0
      else:
        raw_dir = -1.0 if y > 0 else 1.0  # object on right → go left, vice versa

      best_offset = raw_dir * MAX_OFFSET_M * score

    if best_score == 0.0:
      return OffsetResult(offset_m=0.0, trigger_distance=float('inf'), closing_speed=0.0, score=0.0)

    return OffsetResult(
      offset_m=best_offset,
      trigger_distance=best_distance,
      closing_speed=best_closing,
      score=best_score,
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _attr(obj, *names: str, default=None):
  """Return the first matching attribute value from obj, or default."""
  for name in names:
    val = getattr(obj, name, None)
    if val is not None:
      return val
  return default


def _clamp(value: float, lo: float, hi: float) -> float:
  return max(lo, min(hi, value))
