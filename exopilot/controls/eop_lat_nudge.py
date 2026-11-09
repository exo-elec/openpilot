"""LatNudge — lateral obstacle nudge (shared pathd proposer; moved verbatim from EOP10).

Uses stereo ground boundaries and tracked 3D objects to nudge the
vehicle away from obstacles and center it within the drivable corridor.
Operates as a soft overlay on top of the policy curvature trajectory.

Design reference: docs/eop/controllers/enhanced/LAT_NUDGE.md

Inputs (from PathD internal state):
  stereo_left_boundary  — list[float] 7 points at [0,5,10,15,20,25,30]m
  stereo_right_boundary — list[float] 7 points at [0,5,10,15,20,25,30]m
  grid_tracks           — list[TrackedCluster] from ObjectTracker

Output:
  lateral_offsets — list[float] 7 values, one per boundary distance.
  Positive = nudge right (positive Y-axis), negative = nudge left.

Frames (CLAUDE.md "Frame conventions", as sunnypilot/comma): boundaries and
offsets are in the calibrated frame of the path they are added to (y right
positive, left boundary negative). Track yRel is left positive and is
flipped once here.

Safety limits (design §3.5):
  MAX_LATERAL_OFFSET = 0.8 m  — never cross lane boundary
  MAX_AVOIDANCE_SPEED = 80 km/h
  SAFETY_MARGIN = 0.5 m       — clearance from obstacle edge
"""
from __future__ import annotations

from typing import cast

# Distances matching stereoGround leftBoundary / rightBoundary indices
BOUNDARY_DISTANCES = (0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0)

# Safety limits (§3.5)
MAX_LATERAL_OFFSET = 0.8    # m — hard cap, prevent lane crossing
MAX_AVOIDANCE_SPEED = 80.0 / 3.6  # 80 km/h → m/s
SAFETY_MARGIN = 0.5         # m — clearance around obstacles
CENTERING_GAIN = 0.15       # gentle centering pull when path is clear
SMOOTH_ALPHA = 0.12         # EMA filter per update (higher = faster response)

# State IDs (§3.4 state machine)
_DISABLED  = 0
_CENTERING = 1
_AVOIDING  = 2
_RETURNING = 3


class LatNudge:
  """Lateral Nudge — stereo boundary + obstacle lateral adjustment.

  Runs inside PathD at 20 Hz via PathD.update().
  Produces 7-point lateral offset array (m) added to the policy trajectory.
  """

  def __init__(self):
    self.enabled = False
    self.state = _DISABLED

    # Smoothed output — avoids abrupt steering jumps
    self._smoothed_offsets = [0.0] * len(BOUNDARY_DISTANCES)

  # =========================================================================
  # Public API
  # =========================================================================

  def update(self,
             stereo_left: list[float],
             stereo_right: list[float],
             grid_tracks: list,
             v_ego: float) -> list[float]:
    """Compute lateral offsets for enhanced trajectory.

    Args:
      stereo_left:  7 lateral positions of left boundary (m, negative: y right positive)
      stereo_right: 7 lateral positions of right boundary (m, positive)
      grid_tracks:  TrackedCluster list from ObjectTracker
      v_ego:        current ego speed (m/s)

    Returns:
      offsets: 7 floats; add to trajectory Y at each boundary distance.
    """
    if not self.enabled:
      self._smoothed_offsets = [0.0] * len(BOUNDARY_DISTANCES)
      self.state = _DISABLED
      return cast(list[float], self._smoothed_offsets)

    if v_ego > MAX_AVOIDANCE_SPEED:
      # Fade offsets smoothly to zero above speed limit
      self._smoothed_offsets = [o * 0.9 for o in self._smoothed_offsets]
      return cast(list[float], self._smoothed_offsets)

    if len(stereo_left) != 7 or len(stereo_right) != 7:
      self._smoothed_offsets = [0.0] * len(BOUNDARY_DISTANCES)
      return cast(list[float], self._smoothed_offsets)

    raw_offsets = self._compute_offsets(stereo_left, stereo_right, grid_tracks, v_ego)
    self._smooth(raw_offsets)
    self._update_state(raw_offsets)
    return list(self._smoothed_offsets)

  # =========================================================================
  # Core computation
  # =========================================================================

  def _compute_offsets(self,
                       left: list[float],
                       right: list[float],
                       tracks: list,
                       v_ego: float) -> list[float]:
    """Calculate raw lateral offsets at each boundary distance."""
    offsets = []
    for i, dist in enumerate(BOUNDARY_DISTANCES):
      left_edge  = left[i]   # negative (left of ego, y right positive)
      right_edge = right[i]  # positive (right of ego)

      # Default: gentle centering towards corridor midpoint
      mid = (left_edge + right_edge) / 2.0
      offset = mid * CENTERING_GAIN

      # Obstacle nudge: check tracks near this longitudinal distance
      for trk in tracks:
        d_rel = getattr(trk, 'dRel', getattr(trk, 'z', float('inf')))
        # yRel is left positive; flip into the path's frame (right positive)
        y = -getattr(trk, 'yRel', getattr(trk, 'x', 0.0))
        if abs(d_rel - dist) > 3.0:
          continue  # not near this distance slice

        if y < 0:  # track is left of ego
          inner = y + SAFETY_MARGIN  # its side facing ego
          if inner > left_edge:
            # Obstacle is inside left boundary — nudge right
            nudge = inner - left_edge
            if abs(nudge) > abs(offset):
              offset = nudge
        else:  # track is right of ego
          inner = y - SAFETY_MARGIN
          if inner < right_edge:
            # Obstacle is inside right boundary — nudge left
            nudge = inner - right_edge
            if abs(nudge) > abs(offset):
              offset = nudge

      # Hard clamp to safety limit
      offset = max(-MAX_LATERAL_OFFSET, min(MAX_LATERAL_OFFSET, offset))
      offsets.append(offset)

    return offsets

  def _smooth(self, raw: list[float]) -> None:
    """EMA filter to prevent abrupt steering commands."""
    for i in range(len(BOUNDARY_DISTANCES)):
      self._smoothed_offsets[i] += SMOOTH_ALPHA * (raw[i] - self._smoothed_offsets[i])

  def _update_state(self, raw: list[float]) -> None:
    any_avoiding = any(abs(o) > 0.15 for o in raw)
    if any_avoiding:
      self.state = _AVOIDING
    elif self.state == _AVOIDING:
      self.state = _RETURNING
    else:
      self.state = _CENTERING

  # =========================================================================
  # Utility
  # =========================================================================

  @property
  def state_name(self) -> str:
    return {_DISABLED: 'DISABLED', _CENTERING: 'CENTERING',
            _AVOIDING: 'AVOIDING', _RETURNING: 'RETURNING'}.get(self.state, '?')
