"""LonNudge — Stereo-based forward-distance speed trim (PathD module).

Uses stereo-derived drivable distance, occupancy, and lead tracking
to apply soft speed adjustments on top of the policy trajectory.

Design reference: docs/eop/controllers/enhanced/LON_NUDGE.md

Inputs (from PathD internal state):
  drivable_limit_m  — forward drivable distance from stereoGround
  occupancy         — bool: any obstacle in safety corridor
  bike_hazard       — bool: bike/object cut-in detected
  grid_tracks       — list[TrackedCluster] from ObjectTracker
  v_ego, a_ego      — current speed/accel (m/s, m/s²)

Output:
  speed_delta — float (m/s); negative = reduce speed.
  Applied uniformly as an additional speed adjustment to the trajectory.

Safety limits (design §3.5):
  MAX_SPEED_REDUCTION = 30% of current speed
  SMOOTHING_TC = 2.0 s  (EMA time constant)
  Reduction applies only when drivable_limit_m < DRIVABLE_WARN_DIST
"""
from __future__ import annotations

from typing import cast

# Thresholds
DRIVABLE_WARN_DIST  = 40.0   # m — start reducing speed below this
DRIVABLE_STOP_DIST  =  8.0   # m — near-emergency, strong reduction
MAX_SPEED_REDUCTION = 0.30   # 30% cap
SMOOTH_ALPHA        = 0.08   # EMA per update (20 Hz loop)
BIKE_HAZARD_FACTOR  = 0.12   # extra reduction on bike cut-in

# Minimum speed reduction to apply (below this, snap to zero for comfort)
MIN_REDUCTION_APPLY = 0.1   # m/s

# State IDs
_DISABLED      = 0
_CRUISING      = 1
_CAUTION       = 2   # drivable limit shrinking
_BRAKING       = 3   # strong reduction


class LonNudge:
  """Speed Trim — stereo forward-distance speed adjustment.

  Runs inside PathD at 20 Hz via PathD.update().
  Returns a speed_delta (m/s) to subtract from trajectory velocity targets.
  """

  def __init__(self):
    self.enabled = False
    self.state = _DISABLED

    # Smoothed speed adjustment
    self._smoothed_delta = 0.0

  # =========================================================================
  # Public API
  # =========================================================================

  def update(self,
             drivable_limit_m: float,
             occupancy_detected: bool,
             bike_hazard: bool,
             grid_tracks: list,
             v_ego: float,
             a_ego: float) -> float:
    """Compute speed delta (m/s) to apply to trajectory.

    Returns:
      speed_delta: negative float (m/s); add to trajectory velocity targets.
                   Zero when no adjustment needed.
    """
    if not self.enabled:
      self._smoothed_delta = 0.0
      self.state = _DISABLED
      return 0.0

    raw_delta = self._compute_delta(drivable_limit_m, occupancy_detected,
                                    bike_hazard, grid_tracks, v_ego)

    # EMA smoothing
    self._smoothed_delta += SMOOTH_ALPHA * (raw_delta - self._smoothed_delta)

    # Snap to zero if negligible (avoid constant tiny adjustments)
    if abs(self._smoothed_delta) < MIN_REDUCTION_APPLY:
      self._smoothed_delta = 0.0

    self._update_state(drivable_limit_m)
    return cast(float, self._smoothed_delta)

  # =========================================================================
  # Core computation
  # =========================================================================

  def _compute_delta(self,
                     drivable_limit_m: float,
                     occupancy_detected: bool,
                     bike_hazard: bool,
                     grid_tracks: list,
                     v_ego: float) -> float:
    """Calculate raw speed delta (m/s) based on stereo data."""
    if v_ego < 0.5:
      return 0.0  # don't adjust when nearly stopped

    max_allowed_reduction = v_ego * MAX_SPEED_REDUCTION

    # --- Forward drivable distance constraint ---
    dist_factor = 0.0
    if drivable_limit_m < DRIVABLE_WARN_DIST:
      # Linear ramp: full reduction at STOP_DIST, zero at WARN_DIST
      warn_range = DRIVABLE_WARN_DIST - DRIVABLE_STOP_DIST
      dist_factor = (DRIVABLE_WARN_DIST - drivable_limit_m) / warn_range
      dist_factor = max(0.0, min(1.0, dist_factor))

    speed_reduction = dist_factor * max_allowed_reduction

    # --- Occupancy in corridor: add caution bump ---
    if occupancy_detected and dist_factor > 0:
      speed_reduction = min(speed_reduction * 1.2, max_allowed_reduction)

    # --- Bike hazard: extra reduction ---
    if bike_hazard:
      bike_bump = v_ego * BIKE_HAZARD_FACTOR
      speed_reduction = min(speed_reduction + bike_bump, max_allowed_reduction)

    # --- 3D lead vehicle tracking ---
    lead_delta = self._lead_delta(grid_tracks, v_ego)
    if lead_delta < 0:
      speed_reduction = min(speed_reduction - lead_delta, max_allowed_reduction)

    # Return as negative (reduction)
    return -speed_reduction if speed_reduction > 0 else 0.0

  def _lead_delta(self, tracks: list, v_ego: float) -> float:
    """Compute speed delta from closest in-lane lead object."""
    best = None
    for trk in tracks:
      d_rel = getattr(trk, 'dRel', getattr(trk, 'z', float('inf')))
      y_rel = getattr(trk, 'yRel', getattr(trk, 'x', float('inf')))
      v_rel = getattr(trk, 'vRel', getattr(trk, 'vx', 0.0))
      if d_rel < 0 or abs(y_rel) > 1.8:
        continue  # behind or outside lane
      if best is None or d_rel < best[0]:
        best = (d_rel, v_rel)

    if best is None:
      return 0.0

    d_rel, v_rel = best
    if v_rel >= 0:
      return 0.0  # lead is pulling away

    # TTC-based: target 2s following distance
    ttc = d_rel / max(abs(v_rel), 0.1)
    if ttc > 3.0:
      return 0.0  # plenty of room

    safe_dist = 2.0 * v_ego  # 2-second rule
    if d_rel < safe_dist:
      # Proportional reduction to restore safe gap
      gap_error = safe_dist - d_rel
      return -(gap_error * 0.5)  # gentle correction

    return 0.0

  def _update_state(self, drivable_limit_m: float) -> None:
    if drivable_limit_m <= DRIVABLE_STOP_DIST:
      self.state = _BRAKING
    elif drivable_limit_m < DRIVABLE_WARN_DIST:
      self.state = _CAUTION
    else:
      self.state = _CRUISING

  # =========================================================================
  # Utility
  # =========================================================================

  @property
  def state_name(self) -> str:
    return {_DISABLED: 'DISABLED', _CRUISING: 'CRUISING',
            _CAUTION: 'CAUTION', _BRAKING: 'BRAKING'}.get(self.state, '?')
