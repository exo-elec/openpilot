"""
mtsc.py - Map Turn Speed Control (MTSC)

Proactive speed reduction for curves (250-500m range).

Priority:
  1. Learned driver speeds from curved database (if available)
  2. Physics calculation from OSM map curvature (fallback)

Complements VTSC which handles 0-250m range.

Reference: sunnypilot SmartCruiseControlMap + FrogPilot curve physics
"""

import numpy as np
from enum import Enum
from pathlib import Path
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params

# Import learned speed database
from openpilot.selfdrive.controls.lib.eop_utils import (
  get_learned_speed as _common_get_learned_speed,
  calculate_speed_for_curvature,
)


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
  
  def __init__(self):
    self.params = Params()
    self.enabled = False
    self.mapd_enabled = False
    self.curve_learn_enabled = False
    
    # State
    self.state = MTSCState.disabled
    self.frame = 0
    
    # Current calculation
    self.v_target = 0.0
    self.curvature_ahead = 0.0
    self.distance_to_curve = float('inf')
    self.a_comfort = 1.8
    
    # Learned speed tracking
    self.using_learned_speed = False
    self.learned_speed_kph = 0.0
    
    self._update_params()
    
  def _update_params(self):
    """Update parameters periodically."""
    self.enabled = self.params.get_bool("EOPMTSCEnabled")
    self.mapd_enabled = self.params.get_bool("EOPMapdEnabled")
    self.curve_learn_enabled = self.params.get_bool("EOPCurveSpeedLearnEnabled")
    try:
      self.a_comfort = float(self.params.get("EOPTSCTargetLatAccel") or 1.8)
    except (ValueError, TypeError):
      self.a_comfort = 1.8
      
  # EOP-CLEANUP: Removed _get_gps_tolerance() and get_learned_speed() —
  # duplicated in vtsc.py. Now using eop_utils.get_learned_speed().
      
  def extract_upcoming_curvature(self, map_data, v_ego: float) -> list:
    """
    Extract upcoming curves from OSM map data.
    
    Args:
      map_data: MapD data message with OSM geometry
      v_ego: Current speed for time-based filtering
      
    Returns:
      List of (distance, curvature) tuples
    """
    if not map_data or not hasattr(map_data, 'upcomingCurvatureDEPRECATED'):
      return []
      
    curves = []
    for curve in map_data.upcomingCurvatureDEPRECATED:
      distance = curve.x  # Distance along path
      curvature = abs(curve.y)  # Curvature value
      
      # Filter to MTSC range
      if self.MTSC_MIN_DISTANCE <= distance <= self.MTSC_MAX_DISTANCE:
        # Cap curvature for safety
        curvature = np.clip(curvature, self.MIN_CURVATURE, self.MAX_CURVATURE)
        curves.append((distance, curvature))
        
    return curves
    
  # EOP-CLEANUP: calculate_speed_target() moved to eop_utils.calculate_speed_for_curvature().
    
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
      
  def update(self, map_data, v_ego: float, lat: float = 0.0, lon: float = 0.0) -> dict:
    """
    Main update method.
    
    Priority: Learned speed > Physics calculation
    
    Args:
      map_data: MapD data message
      v_ego: Current speed
      lat, lon: GPS coordinates (for learned speed lookup)
      
    Returns:
      Dict with v_target, state, is_active, handover_to_vtsc, using_learned
    """
    # Update params periodically (once per second)
    if self.frame % int(1.0 / DT_MDL) == 0:
      self._update_params()
    
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
    upcoming_curves = self.extract_upcoming_curvature(map_data, v_ego)
    
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
    learned_speed_ms = _common_get_learned_speed(
      lat, lon, curvature, self.curve_learn_enabled, min_speed_kph=20.0
    )

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
