"""MTSC - Map Turn Speed Control (proactive curve speed from OSM curvature, 150-500 m).

Moved from EOP10's `selfdrive/controls/lib/mtsc.py` with its logic unchanged (golden-tested against recorded outputs of the
original). Differences: no Params/realtime (configuration is passed in; the adapter reads params), the learned-speed lookup is
injected, and `calculate_speed_for_curvature` lives in curve_speed.py. Pure: no cereal.
"""
from enum import Enum

import numpy as np

from exopilot.selfdrive.controls.lib.curve_speed import calculate_speed_for_curvature


class MTSCState(Enum):
  """MTSC operating states."""
  disabled = 0
  approaching = 1
  handover = 2  # Handing over to VTSC


class MTSC:
  """
  Map Turn Speed Controller

  Proactive speed reduction based on OSM curve data (250-500m range).
  Complements VTSC which handles 0-250m range.
  """

  # Range constants
  # MTSC operates from 500m down to 150m
  # VTSC takes over from 150m to 0m (vision is most precise close up)
  MTSC_MIN_DISTANCE = 150  # meters - handover to VTSC (was 250m)
  MTSC_MAX_DISTANCE = 500  # meters - max lookahead

  # Thresholds
  MIN_CURVATURE = 0.001  # 1/m - ignore straighter than this
  MAX_CURVATURE = 0.1    # 1/m - cap for safety

  def __init__(self, enabled: bool = True, curve_learn_enabled: bool = False, a_comfort: float = 1.8, learned_speed=None):
    """Configuration is passed in (the adapter reads params). `learned_speed(lat, lon, curvature) -> m/s` is the curve-database
    lookup (0 = none); the default has no database."""
    self.enabled = enabled
    self.mapd_enabled = True
    self.curve_learn_enabled = curve_learn_enabled
    self._learned_speed = learned_speed or (lambda lat, lon, curvature: 0.0)

    # State
    self.state = MTSCState.disabled
    self.frame = 0

    # Current calculation
    self.v_target = 0.0
    self.curvature_ahead = 0.0
    self.distance_to_curve = float('inf')
    self.a_comfort = a_comfort

    # Learned speed tracking
    self.using_learned_speed = False
    self.learned_speed_kph = 0.0

  # EOP-CLEANUP: Removed _get_gps_tolerance() and get_learned_speed() —
  # duplicated in vtsc.py. Now using speed_utils.get_learned_speed().

  def extract_upcoming_curvature(self, curves) -> list:
    """The (distance, curvature) pairs within MTSC's range, curvature capped."""
    return [(d, float(np.clip(abs(k), self.MIN_CURVATURE, self.MAX_CURVATURE)))
            for d, k in curves if self.MTSC_MIN_DISTANCE <= d <= self.MTSC_MAX_DISTANCE]

  # EOP-CLEANUP: calculate_speed_target() moved to speed_utils.calculate_speed_for_curvature().

  def update_state_machine(self, has_upcoming_curve: bool, distance_to_curve: float):
    """Update MTSC state machine."""
    if self.state == MTSCState.disabled:
      if has_upcoming_curve and distance_to_curve > self.MTSC_MIN_DISTANCE:
        self.state = MTSCState.approaching

    elif self.state == MTSCState.approaching:
      if not has_upcoming_curve:
        self.state = MTSCState.disabled
      elif distance_to_curve <= self.MTSC_MIN_DISTANCE:
        self.state = MTSCState.handover

    elif self.state == MTSCState.handover:
      # VTSC takes over - return to disabled
      self.state = MTSCState.disabled

  def update(self, curves, v_ego: float, lat: float = 0.0, lon: float = 0.0) -> dict:
    """
    Main update method.

    Priority: Learned speed > Physics calculation

    Args:
      curves: (distance ahead m, curvature 1/m) pairs along the road (curve_speed.curves_from_path)
      v_ego: Current speed
      lat, lon: GPS coordinates (for learned speed lookup)

    Returns:
      Dict with v_target, state, is_active, handover_to_vtsc, using_learned
    """
    self.using_learned_speed = False
    self.learned_speed_kph = 0.0

    # Check if MTSC should be active
    if not self.enabled or not self.mapd_enabled:
      self.state = MTSCState.disabled
      return {
        'v_target': None,
        'state': self.state.name,
        'is_active': False,
        'handover_to_vtsc': False,
        'curvature': 0.0,
        'distance': float('inf'),
        'using_learned': False
      }

    # Extract upcoming curves
    upcoming_curves = self.extract_upcoming_curvature(curves)

    if not upcoming_curves:
      self.update_state_machine(False, float('inf'))
      return {
        'v_target': None,
        'state': self.state.name,
        'is_active': False,
        'handover_to_vtsc': False,
        'curvature': 0.0,
        'distance': float('inf'),
        'using_learned': False
      }

    # Find most restrictive curve in range (highest curvature = lowest speed)
    most_restrictive = max(upcoming_curves, key=lambda x: x[1])
    distance, curvature = most_restrictive

    self.curvature_ahead = curvature
    self.distance_to_curve = distance

    # Try learned speed first (priority over physics)
    learned_speed_ms = self._learned_speed(lat, lon, curvature) if self.curve_learn_enabled else 0.0

    if learned_speed_ms > 0:
      # Use learned driver speed
      self.v_target = learned_speed_ms
      self.using_learned_speed = True
      self.learned_speed_kph = learned_speed_ms * 3.6
    else:
      # Fall back to physics calculation
      self.v_target = calculate_speed_for_curvature(curvature, self.a_comfort, self.MIN_CURVATURE)
      self.using_learned_speed = False

    # Update state machine
    self.update_state_machine(True, distance)

    is_active = self.state == MTSCState.approaching
    handover = self.state == MTSCState.handover

    self.frame += 1

    return {
      'v_target': self.v_target if is_active else None,
      'state': self.state.name,
      'is_active': is_active,
      'handover_to_vtsc': handover,
      'curvature': self.curvature_ahead,
      'distance': self.distance_to_curve,
      'using_learned': self.using_learned_speed
    }
