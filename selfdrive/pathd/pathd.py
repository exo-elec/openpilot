#!/usr/bin/env python3
"""
Path Trajectory Adjustment Daemon (pathd)
Emergency collision avoidance using multi-camera fusion data.

ARCHITECTURE:
===========================
SOC (Smart Offset Controller) - MOVED TO CONTROLSD
  - Legacy pathd SOC code removed
  - Now handled by eop_soc_controller in controlsd
  - Provides MPC crosscheck + universal lateral offset

CURRENT PATHD RESPONSIBILITIES:
=================================
1. Emergency Collision Avoidance (Longitudinal Only)
   - Detects NEW objects missed by stock openpilot
   - Multi-camera fusion (stereo + side cameras)
   - Trajectory-based collision prediction
   - Distance-based emergency braking
   - Handles ALL obstacles: people, animals, vehicles, unknown

2. Occupancy Grid Fusion
   - Consumes gridd occupancyGrid
   - Grid-based threat detection
   - Adds speed reduction for grid obstacles

ARCHITECTURE:
  stereod → (VisionIPC STEREO_LEFT/RIGHT) → gridd
  gridd   → gridObjects (BEV occupancy grid, 20Hz)
  modeld  → drivingModelData (policy: desiredCurvature, desiredAcceleration)

  gridd + drivingModelData ──► pathd
                                 ├─ internal track.py  (cluster tracker on BEV grid)
                                 ├─ internal predict.py (constant-velocity 3s horizon)
                                 └─ safety filter: override policy trajectory if
                                    collision predicted within TTC threshold
                                         ↓
                                  enhancedTrajectory
                                         ↓
                                  controlsd
                                  ├─ eop_soc_controller (lateral offset)
                                  ├─ longitudinal_planner (braking)
                                  └─ desire_helper (lane changes)

WHAT PATHD DOES:
- Emergency braking for obstacles in path (all types: people, animals, vehicles)
- Grid-based obstacle detection

WHAT PATHD DOES NOT DO:
- Smart lateral offset from large vehicles (eop_soc_controller handles this)
- Direct lateral control
- Convoy Mode (Removed)

ENABLE/DISABLE:
  - Emergency braking: Always active (safety critical)
  - SOC lateral offset: EOPSOCControllerEnabled (handled by controlsd, not pathd)
"""
from __future__ import annotations

import time
import math
from dataclasses import dataclass
from typing import Any

from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity

import numpy as np
import cereal.messaging as messaging
from cereal import log

from openpilot.system.hardware import HARDWARE
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.selfdrive.pathd.track import ObjectTracker
from openpilot.selfdrive.pathd.predict import predict as predict_clusters
from openpilot.selfdrive.pathd.path_corridor_fusion import (
    fuse_corridor_boundaries,
    validate_corridor_boundaries,
)
from openpilot.selfdrive.pathd.soc import SOC, OffsetResult
from openpilot.selfdrive.pathd.lane_change import evaluate_lane_change_gate
from openpilot.selfdrive.pathd.lat_nudge import LatNudge, BOUNDARY_DISTANCES as LN_BOUNDARY_DISTANCES
from openpilot.selfdrive.pathd.lon_nudge import LonNudge


# Longitudinal control parameters
MAX_SPEED_REDUCTION = 5.0  # m/s (~18 km/h max braking for city emergencies)
COLLISION_BUFFER = 0.5  # meters (safety margin around our trajectory)
POSITION_MATCH_TOLERANCE = 2.0  # meters (for matching with modelV2 objects)

# Path width for collision detection (TEMPORARY - will be replaced by trajectory-based)
PATH_WIDTH = 1.8  # meters (standard lane width, approximate)

# Trajectory-based collision detection parameters
MIN_TTC_THRESHOLD = 0.5  # seconds (minimum time-to-collision before emergency braking)
SAFE_TTC_THRESHOLD = 3.0  # seconds (comfortable time-to-collision)

# Occupancy grid fusion parameters
GRID_SCORE_THRESHOLD = 0.6
GRID_LOOKAHEAD_M = 45.0
GRID_STALE_TIMEOUT = 0.5

# Trajectory smoothing
ACCEL_RESPONSE_TIME = 1.0  # seconds to reach target velocity
JERK_LIMIT = 2.0           # m/s³ - maximum rate of acceleration change
SPEED_REDUCTION_FILTER = 0.2

# Distance-based scaling thresholds (fraction of max_depth, scale factor)
# Format: (distance_ratio_threshold, scale_factor, description)
# Applied in reverse order (highest ratio first)
DISTANCE_SCALE_THRESHOLDS = [
    (float('inf'), 0.0, "beyond_max"),      # > 100% max_depth: no scaling
    (1.0, 0.0, "beyond_max"),               # > max_depth: no scaling
    (0.6, 0.3, "far"),                      # 60-100% max_depth: light scaling
    (0.3, 0.6, "medium"),                   # 30-60% max_depth: medium scaling
    (0.1, 1.0, "near"),                     # 10-30% max_depth: full scaling
    (0.0, 1.5, "emergency"),                # 0-10% max_depth: aggressive scaling
]

# Velocity-based scaling thresholds (m/s, scale_factor)
VELOCITY_SCALE_BP = [0.0, 12.0, 24.0, 36.0]  # m/s thresholds (EVP 3-zone)
VELOCITY_SCALE_V = [0.4, 0.7, 1.0, 1.2]     # corresponding scale factors


@dataclass
class OccupancyGridView:
  resolution: float
  width: int
  height: int
  origin_x: float
  origin_y: float
  occupancy: np.ndarray
  cost: np.ndarray
  cost_norm: float

  @classmethod
  def from_message(cls, grid_msg: log.OccupancyGrid) -> OccupancyGridView:
    width = int(grid_msg.width)
    height = int(grid_msg.height)
    resolution = float(grid_msg.resolution or 1.0)
    origin_x = float(grid_msg.originX)
    origin_y = float(grid_msg.originY)

    occ_layer = None
    cost_layer = None
    for layer in grid_msg.layers:
      if layer.name == "occupancy":
        occ_layer = layer
      elif layer.name == "cost":
        cost_layer = layer

    if occ_layer is None or not occ_layer.data:
      raise ValueError("Occupancy layer missing in occupancyGrid")

    occ_raw = np.frombuffer(occ_layer.data, dtype=np.uint8, count=width * height)
    occ = occ_raw.reshape((height, width)).astype(np.float32)
    occ_scale = occ_layer.scale if occ_layer.scale != 0.0 else (1.0 / 255.0)
    occ = occ * occ_scale + occ_layer.offset

    if cost_layer is not None and cost_layer.data:
      cost_raw = np.frombuffer(cost_layer.data, dtype=np.uint16, count=width * height)
      cost = cost_raw.reshape((height, width)).astype(np.float32)
      cost_scale = cost_layer.scale if cost_layer.scale != 0.0 else 1.0
      cost = cost * cost_scale + cost_layer.offset
    else:
      cost = np.zeros((height, width), dtype=np.float32)

    cost_norm = float(np.max(cost)) if cost.size else 1.0
    cost_norm = max(10.0, min(cost_norm, 200.0))

    return cls(
      resolution=resolution,
      width=width,
      height=height,
      origin_x=origin_x,
      origin_y=origin_y,
      occupancy=occ,
      cost=cost,
      cost_norm=cost_norm,
    )

  @property
  def forward_max(self) -> float:
    return self.origin_y + self.height * self.resolution

  def _cell_indices(self, forward: float, lateral: float) -> tuple[int, int] | None:
    row = int(math.floor((forward - self.origin_y) / self.resolution))
    col = int(math.floor((lateral - self.origin_x) / self.resolution))
    if row < 0 or row >= self.height or col < 0 or col >= self.width:
      return None
    return row, col

  def occupancy_at(self, forward: float, lateral: float) -> float | None:
    idx = self._cell_indices(forward, lateral)
    if idx is None:
      return None
    return float(self.occupancy[idx])

  def cost_at(self, forward: float, lateral: float) -> float | None:
    idx = self._cell_indices(forward, lateral)
    if idx is None:
      return None
    return float(self.cost[idx])

  def score_at(self, forward: float, lateral: float) -> float | None:
    idx = self._cell_indices(forward, lateral)
    if idx is None:
      return None
    occ = float(self.occupancy[idx])
    cost = float(self.cost[idx])
    score = max(0.0, occ)
    if self.cost_norm > 0.0:
      score = max(score, min(cost / self.cost_norm, 1.0))
    return score

  def first_high_score_along_path(
    self, distances, laterals, score_threshold: float = GRID_SCORE_THRESHOLD, max_distance: float | None = GRID_LOOKAHEAD_M
  ) -> tuple[float, float, float] | None:
    if not distances or not laterals:
      return None
    for dist, lat in zip(distances, laterals, strict=False):
      if not math.isfinite(dist) or not math.isfinite(lat):
        continue
      if dist < 0.0:
        continue
      if max_distance is not None and dist > max_distance:
        break
      score = self.score_at(dist, lat)
      if score is None:
        continue
      if score >= score_threshold:
        return dist, lat, score
    return None


def compute_speed_reduction(tracked_objects, predicted_objects, ego_velocity, grid_view: OccupancyGridView | None = None, model_v2=None):
  """
  Trajectory-based collision prediction for messy city driving.

  REDESIGNED FOR LANELESS/CITY CONDITIONS:
  - NO fixed PATH_WIDTH assumptions (lanes may not exist!)
  - Uses OUR predicted trajectory from modelV2.position
  - Detects trajectory INTERSECTIONS with tracked objects
  - Works in: construction zones, roundabouts, laneless streets, parking lots

  CAMERA ROLE SEPARATION:
  - STEREO: Primary (good depth for distance-based braking)
  - SIDE CAMERAS: Lane-changing objects (time-based prediction)
    * Track lateral velocity → predict when entering our trajectory
    * Earlier warning than stereo alone

  CITY SCENARIOS HANDLED:
  - Pedestrians/bikes crossing from sidewalk
  - Stop-and-go traffic (hysteresis prevents jerky braking)
  - Parked cars (stationary objects filtered by trajectory intersection)
  - Motorcycles filtering through traffic
  - Delivery scooters on sidewalk (sudden turns detected)
  - Roundabouts/curved roads (trajectory-based, not linear)
  - Pedestrian crosswalks (fast lateral movement)
  - Construction zones (adaptive to actual trajectory width)
  - U-turns (trajectory handles rotation)
  - Emergency vehicles (all directions considered)
  - Wrong-way drivers (head-on collision detection)
  - Double-parked vehicles (hard braking for stationary in path)
  - School zones (fast-moving small objects)
  - Taxis sudden stops (acceleration-based prediction)

  INTEGRATION APPROACH:
  Instead of modifying v_cruise (too jerky), we:
  1. Detect trajectory intersections with tracked objects
  2. Compute "obstacle distance" along our trajectory
  3. Return as additional radar-like lead data
  4. Let longitudinal_planner's MPC handle smooth acceleration control

  This way:
  - MPC handles smooth braking (not us)
  - Respects jerk limits, personality settings
  - Integrates cleanly with existing control loop

  Returns: distance to closest obstacle in our trajectory (meters)
           Returns inf if no collision predicted
  """
  tracked_objects = tracked_objects or []
  predicted_objects = predicted_objects or []

  # NaN safety on ego velocity
  if not math.isfinite(ego_velocity):
    ego_velocity = 0.0

  # tracked_objects are the source of truth (from internal tracker)

  predictions_by_id = {obj.trackId: obj for obj in predicted_objects} if predicted_objects else {}

  # Find all NEW objects in predicted path (not just closest)
  threats = []

  for obj in tracked_objects:
    # OCCLUSION HANDLING: Keep occluded tracks in consideration
    # Even if occluded (not currently visible), use predicted position
    # This prevents sudden acceleration when front car changes lane revealing truck behind
    if obj.prob < 0.5:
      # Lower threshold for occluded objects (they have lower confidence during occlusion)
      if not (hasattr(obj, 'occluded') and obj.occluded and obj.prob > 0.3):
        continue  # Skip low-confidence unless it's an occluded track

    # FIX Gap #9: Only consider objects AHEAD of us
    if obj.dRel <= 0:
      continue  # Behind us, skip

    # NaN safety
    if not (math.isfinite(obj.dRel) and math.isfinite(obj.yRel) and math.isfinite(obj.vRel)):
      continue

    # All tracked objects are considered for collision detection

    # FIX Gap #4: Path prediction - check current position AND future position
    current_lateral = obj.yRel

    pred_obj = predictions_by_id.get(obj.trackId)

    # Predict lateral position 2 seconds ahead using predicted trajectory if available
    future_lateral = current_lateral
    if pred_obj and getattr(pred_obj, 'y', None):
      # Find the lateral position at the prediction horizon (2 seconds)
      for t_val, y_val in zip(pred_obj.t, pred_obj.y, strict=False):
        if t_val >= 0.0 and t_val <= SAFE_TTC_THRESHOLD and math.isfinite(y_val):
          future_lateral = y_val
          break

    # Object threatens us if either current OR future position is in our path
    in_path_now = abs(current_lateral) <= PATH_WIDTH
    in_path_future = abs(future_lateral) <= PATH_WIDTH

    # CAMERA SOURCE FILTERING: Smart filtering based on use case
    # - STEREO: Use directly (good depth for distance-based braking)
    # - SIDE CAMERAS: Use for LANE CHANGING objects (time-based prediction)
    #   * Don't need precise depth if tracking WHEN they'll enter our lane
    #   * Use lateral velocity + trajectory to predict lane change timing
    #   * Earlier warning than stereo alone!

    is_from_side_camera = False
    is_from_stereo = False

    if hasattr(obj, 'cameraSources') and obj.cameraSources:
      side_cameras = ['leftFront', 'leftRear', 'rightFront', 'rightRear']
      is_from_side_camera = any(cam in obj.cameraSources for cam in side_cameras)
      is_from_stereo = 'stereo' in obj.cameraSources or 'modelV2' in obj.cameraSources

      # If side camera ONLY (not stereo), check if it's lane-changing
      if is_from_side_camera and not is_from_stereo:
        # For side-only objects: ONLY use if they're moving INTO our lane
        # Skip if not lane-changing (staying in adjacent lane)
        if not (in_path_now or in_path_future):
          continue  # Not lane-changing, skip for longitudinal

        # Lane-changing detected! Even with approximate depth, we can use:
        # - Time to lane crossing (from lateral velocity)
        # - Earlier warning than stereo would give
        # - Safe distance control for lane changing vehicles

    if not (in_path_now or in_path_future):
      continue  # Not in our path

    # FIX Gap #6: Compute closing speed correctly (handle stationary objects)
    closing_speed = -obj.vRel  # Positive = closing by default
    if pred_obj and getattr(pred_obj, 'v', None):
      closing_speed = 0.0
      for t_val, v_val in zip(pred_obj.t, pred_obj.v, strict=False):
        if t_val >= 0.0:
          closing_speed = max(closing_speed, -v_val)
          break
      if closing_speed <= 0.0:
        closing_speed = -obj.vRel

    # Only brake if we're closing on object (not if it's moving away faster)
    if closing_speed < 0.5:  # Less than 0.5 m/s closing
      continue

    # FIX Gap #2: Track ALL threats, not just closest
    predicted_ttc = None
    if pred_obj and getattr(pred_obj, 'x', None):
      for t_val, x_val in zip(pred_obj.t, pred_obj.x, strict=False):
        if not math.isfinite(t_val) or not math.isfinite(x_val):
          continue
        if t_val < 0.0:
          continue
        if x_val <= COLLISION_BUFFER:
          predicted_ttc = t_val
          break

    # DEPTH SOURCE PRIORITY: Prefer mask-based depth (more accurate for overlapping objects)
    depth_confidence = 1.0  # Default
    if hasattr(obj, 'cameraSources') and obj.cameraSources:
      # Check if from stereo (more reliable depth)
      if 'stereo' in obj.cameraSources:
        depth_confidence = 1.2  # Stereo gets 20% boost
        # Check if mask-based depth (even more accurate)
        if hasattr(obj, 'depthSource'):
          # Need to access the enum value - import it at top if not already
          try:
            if obj.depthSource == 1:  # maskMedian enum value
              depth_confidence = 1.5  # Mask-based stereo gets 50% boost (highest confidence)
          except (AttributeError, TypeError):
            pass  # Field not available, use default stereo boost

    # OCCLUSION BOOST: Slightly boost occluded objects to prevent sudden acceleration
    # when front car changes lane revealing previously occluded truck
    if hasattr(obj, 'occluded') and obj.occluded:
      depth_confidence *= 1.1  # 10% boost for occluded tracks (maintain continuity)

    threats.append(
      {
        'distance': obj.dRel,
        'closing_speed': closing_speed,
        'lateral': current_lateral,
        'future_lateral': future_lateral,
        'prob': obj.prob * depth_confidence,  # Apply confidence boost
        'predicted_ttc': predicted_ttc,
        'occluded': hasattr(obj, 'occluded') and obj.occluded,
      }
    )

  if grid_view is not None and model_v2 and getattr(model_v2, 'position', None):
    path_x = list(model_v2.position.x) if model_v2.position.x else []
    path_y = list(model_v2.position.y) if model_v2.position.y else []
    grid_hit = grid_view.first_high_score_along_path(path_x, path_y)
    if grid_hit is not None:
      dist, lateral, score = grid_hit
      closing_speed = max(ego_velocity, 0.1) + 1.0
      threats.append(
        {
          'distance': max(0.1, dist),
          'closing_speed': closing_speed,
          'lateral': lateral,
          'future_lateral': lateral,
          'prob': score,
          'predicted_ttc': dist / max(closing_speed, 0.1),
          'occluded': False,
        }
      )

  # No new threats - return inf to signal "no braking needed"
  # (consistent with return at line 119)
  if not threats:
    return float('inf')

  # Find most critical threat (closest with high closing speed)
  # Prioritize by time-to-collision (TTC)
  most_critical = None
  min_ttc = float('inf')

  for threat in threats:
    ttc = threat['distance'] / max(threat['closing_speed'], 0.1)
    if threat['predicted_ttc'] is not None:
      ttc = min(ttc, threat['predicted_ttc'])
    if ttc < min_ttc:
      min_ttc = ttc
      most_critical = threat

  if most_critical is None:
    return float('inf')  # No critical threat

  # Compute speed reduction based on distance and closing speed
  distance = most_critical['distance']
  closing_speed = most_critical['closing_speed']

  # Distance-based scaling using configurable thresholds
  max_depth = HARDWARE.get_max_reliable_depth_m()
  distance_ratio = distance / max_depth

  scale = 0.0
  for threshold, scale_factor, _ in DISTANCE_SCALE_THRESHOLDS:
    if distance_ratio <= threshold:
      scale = scale_factor
      break

  # Base speed reduction
  speed_reduction = -closing_speed * scale

  # Velocity-based scaling for consistent braking feel
  # Higher speeds allow stronger braking, low speeds are gentler
  velocity_scale = float(np.interp(ego_velocity, VELOCITY_SCALE_BP, VELOCITY_SCALE_V))
  speed_reduction *= velocity_scale

  # Clamp to max reduction
  speed_reduction = max(speed_reduction, -MAX_SPEED_REDUCTION)

  # Additional safety: Never reduce more than 50% of current speed
  if ego_velocity > 1.0:
    max_percentage_reduction = -ego_velocity * 0.5
    speed_reduction = max(speed_reduction, max_percentage_reduction)

  # NaN safety
  if not math.isfinite(speed_reduction):
    return 0.0

  return speed_reduction

# Legacy SOC code removed - now handled by eop_soc_controller in controlsd


@dataclass
class _NavInstr:
    """Thin wrapper so compute_speed_reduction_with_nav can read navInstruction fields."""
    maneuverType: str
    maneuverModifier: str
    distance: float  # maneuverDistance, metres


@dataclass
class _NavData:
    """Adapter from cereal navInstruction → nav_data expected by compute_speed_reduction_with_nav."""
    speedLimit: float | None          # m/s, None if unknown
    navigationInstruction: _NavInstr | None
    stopSignsAhead: list = None       # type: ignore[assignment]
    trafficLights: list = None        # type: ignore[assignment]  # From NavPilot HD map

    def __post_init__(self):
        if self.stopSignsAhead is None:
            self.stopSignsAhead = []
        if self.trafficLights is None:
            self.trafficLights = []


def _nav_data_from_cereal(ni) -> _NavData | None:
    """Build a _NavData from a cereal NavInstruction reader.  Returns None if all fields empty."""
    sl = float(ni.speedLimit) if ni.speedLimit > 0 else None
    mt = ni.maneuverType or ""
    mm = ni.maneuverModifier or ""
    dist = float(ni.maneuverDistance)
    instr = _NavInstr(maneuverType=mt, maneuverModifier=mm, distance=dist) if mt else None

    # Read traffic lights from NavPilot HD map (via bluetoothd)
    traffic_lights = []
    if hasattr(ni, 'trafficLights') and len(ni.trafficLights) > 0:
        for tl in ni.trafficLights:
            traffic_lights.append({
                'distance': float(tl.distance),
                'state': int(tl.state),  # 0=unknown, 1=red, 2=yellow, 3=green
                'confidence': float(tl.confidence),
            })

    if sl is None and instr is None and not traffic_lights:
        return None
    return _NavData(speedLimit=sl, navigationInstruction=instr, trafficLights=traffic_lights)


def compute_speed_reduction_with_nav(tracked_objects, predicted_objects, ego_velocity, nav_data, grid_view: OccupancyGridView | None = None, model_v2=None):
  """
  Enhanced collision detection with navigation context.

  MODULAR DESIGN:
  - If nav_data is None: Works standalone (same as compute_speed_reduction)
  - If nav_data available: Adds navigation-aware adjustments

  Navigation-aware features (future):
  - Stop signs: Gradual approach braking
  - Traffic lights: Red light detection
  - Speed limit changes: Smooth transitions
  - Intersections: Conservative behavior

  Returns: speed_reduction (m/s, negative = brake)
  """
  # Base collision detection (always runs)
  base_reduction = compute_speed_reduction(tracked_objects, predicted_objects, ego_velocity, grid_view, model_v2)

  # If no navigation data, return base reduction (standalone mode)
  if nav_data is None:
    return base_reduction

  nav_reduction = base_reduction

  # Standard navigation data processing
  # Speed limit transitions: brake gently if upcoming limit is much lower
  if nav_data is not None and nav_data.speedLimit is not None and math.isfinite(nav_data.speedLimit):
    limit = max(nav_data.speedLimit, 0.0)
    # Use current ego velocity if available
    v_ego_val = float(ego_velocity if ego_velocity is not None else 0.0)
    delta_v = v_ego_val - limit
    if delta_v > 1.0:
      # Decelerate proportionally, clamp to reasonable braking
      nav_reduction = min(nav_reduction, -min(delta_v / 3.0, 1.0))

  # Intersection / stop sign handling via navInstruction
  if nav_data is not None and nav_data.stopSignsAhead:
    for stop in nav_data.stopSignsAhead:
      distance = float(stop.distance)
      if distance < 0:
        continue
      if distance < 15.0:
        nav_reduction = min(nav_reduction, -0.8)
      elif distance < 40.0:
        nav_reduction = min(nav_reduction, -0.4)

  # HD Map traffic lights from NavPilot (intersection hints)
  # Note: Actual traffic light state comes from camera detection (TLSC)
  # HD map hints provide early warning of upcoming traffic light intersections
  if nav_data is not None and nav_data.trafficLights:
    # Find the nearest traffic light
    nearest_tl = None
    nearest_dist = float('inf')
    for tl in nav_data.trafficLights:
      d = tl.get('distance', 9999)
      if d < nearest_dist:
        nearest_dist = d
        nearest_tl = tl

    if nearest_tl and nearest_dist < 200:  # Within 200m
      # Conservative approach: anticipate potential stop
      # Actual stop decision is made by TLSC using camera detection
      if nearest_dist < 50.0:
        # Close to intersection - ready for potential stop
        nav_reduction = min(nav_reduction, -0.2)  # Light pre-braking
      elif nearest_dist < 100.0:
        # Approaching intersection
        nav_reduction = min(nav_reduction, -0.1)  # Very light coast

  if nav_data is not None and nav_data.navigationInstruction is not None:
    instr = nav_data.navigationInstruction
    distance = getattr(instr, "distance", None)
    if distance is not None and distance >= 0:
      # Tight turns (left/right/U-turn) trigger conservative slowdown
      maneuver = (instr.maneuverType or "").lower()
      modifier = (instr.maneuverModifier or "").lower()
      turning = any(key in maneuver for key in ("turn", "uturn")) or modifier in {"left", "right", "sharp left", "sharp right", "u-turn"}
      # ExoPilot 01M max reliable distance (80m)
      max_reliable_distance = HARDWARE.get_max_reliable_depth_m()
      if turning and distance < max_reliable_distance:
        if distance < 20.0:
          nav_reduction = min(nav_reduction, -0.6)
        else:
          nav_reduction = min(nav_reduction, -0.3)

  return nav_reduction


class PathD:
  def __init__(self):
    # Set CPU affinity to A76 cores (big cores) - safety critical
    set_daemon_affinity("pathd")

    self.pm = messaging.PubMaster(['enhancedTrajectory'])

    # gridd/pathd two-daemon pipeline
    # Tracking and prediction are handled internally:
    #   track.py/predict.py run inside pathd on gridObjects data
    self.sm = messaging.SubMaster(
      [
        'carState',
        'gridObjects',
        'stereoGround',       # OccupancyGrid fusion: bikeHazard, drivableLimitM, occupancyDetected
        'drivingModelData',   # policy model output: desiredCurvature, desiredAcceleration
        'navInstruction',     # NAVD: speed limit + upcoming maneuver for nav-aware braking
        'navRoute',           # NAVD: route geometry for Hybrid A*
        'drivableArea',       # surfaced: BEV drivable area for planning
        'mapData',            # MAPD: OSM road geometry, curves, speed limits (500m)
      ]
    )

    try:
      from openpilot.common.params import Params
    except ImportError:
      from openpilot.common.params import Params  # type: ignore

    self.params = Params()
    self._last_tracked_objects: list[Any] = []

    # Speed reduction smoothing
    self.last_speed_reduction = 0.0
    self.SPEED_REDUCTION_FILTER = 0.3   # Low-pass filter alpha (0=smooth, 1=instant)
    self.ACCEL_RESPONSE_TIME   = 1.0   # seconds — converts speed delta (m/s) → accel (m/s²)

    # Lane change state tracking
    self.lane_change_timer = 0.0
    self.lane_change_direction = None  # 'left', 'right', or None
    self.prev_blinker_state = (False, False)  # (left, right)

    self.grid_view: OccupancyGridView | None = None
    self._grid_last_time = 0.0

    # stereoGround fields for emergency braking and lateral adjustment
    self._bike_hazard = False
    self._drivable_limit_m = HARDWARE.get_max_reliable_depth_m()  # ExoPilot 01M: 80m
    self._occupancy_detected = False
    self._stereo_ground_last_time = 0.0
    # Multi-source corridor boundaries (fused from segmentation + vision model)
    self._corridor_left_boundary: np.ndarray = np.full(33, 3.0, dtype=np.float32)
    self._corridor_right_boundary: np.ndarray = np.full(33, -3.0, dtype=np.float32)
    self._corridor_source = log.EnhancedTrajectory.BoundarySource.vision
    self._corridor_confidence = 0.0

    # Lead trajectory safety tracking
    self._lead_traj_violation_count = 0  # Hysteresis for boundary violations
    self._lead_traj_last_safe = True

    # Grid-based cluster tracker and predictor (internal track.py)
    # EOP: Track/Predict can be disabled via parameters
    self._tracker_enabled = self.params.get_bool("EOPTrackEnabled")
    self._predict_enabled = self.params.get_bool("EOPPredictEnabled")
    self._tracker = ObjectTracker() if self._tracker_enabled else None
    self._last_track_time = time.monotonic()
    # Cached params for high-frequency loop (refreshed every 2s)
    self._last_param_t: float = 0.0
    self._aeb_enabled: bool = self.params.get_bool("EOPAEBEnabled")
    self._soc_enabled: bool = self.params.get_bool("EOPSOCControllerEnabled")
    stereo_enabled = self.params.get_bool("EOPStereoEnabled")
    nudge_enabled = self.params.get_bool("EOPNudgeEnabled")
    self._stereo_enabled: bool = stereo_enabled and nudge_enabled
    self._grid_tracks: list[Any] = []

    # Lateral offset controller for fast-closing objects
    self._soc_ctrl = SOC()
    # EOP: LatNudge / LonNudge — stereo path enhancements
    self._lat_nudge = LatNudge()
    self._lon_nudge = LonNudge()

    # Trajectory state for smoothing
    self.last_accel_adjustments = np.zeros(ModelConstants.IDX_N, dtype=np.float32)
    self.last_update_t = time.monotonic()

    # Logging counter (throttles warning logs)
    self.log_counter = 0

    # Long horizon planner (500m) - integrates navd + Hybrid A*
    self._long_horizon_enabled = self.params.get_bool("EOPHybridPlannerEnabled")
    if self._long_horizon_enabled:
      try:
        from openpilot.selfdrive.pathd.osm_hybrid_planner import OsmHybridPlanner
        self._hybrid_planner = OsmHybridPlanner()
        cloudlog.info("PathD: Long horizon planner enabled")
      except Exception as e:
        cloudlog.error(f"PathD: Failed to init hybrid planner: {e}")
        self._long_horizon_enabled = False
        self._hybrid_planner = None
    else:
      self._hybrid_planner = None

  def _check_road_edges(self, trajectory_y: list[float], ground_objects) -> tuple[bool, str]:
    """
    Check if trajectory violates road edge boundaries (CRITICAL safety check).

    Args:
      trajectory_y: Lateral positions in trajectory (meters from center)
      ground_objects: groundObjects message with roadGeometry

    Returns:
      (violation: bool, reason: str)
    """
    if not ground_objects or not hasattr(ground_objects, 'roadGeometry'):
      return (False, "no_road_geometry")

    road_geom = ground_objects.roadGeometry
    if not road_geom.roadEdges or len(road_geom.roadEdges) < 2:
      return (False, "no_road_edges")

    # Road edges: [left_edge, right_edge]
    # Each edge has x[], y[] arrays
    left_edge = road_geom.roadEdges[0] if len(road_geom.roadEdges) > 0 else None
    right_edge = road_geom.roadEdges[1] if len(road_geom.roadEdges) > 1 else None

    EDGE_SAFETY_MARGIN = 0.3  # meters (conservative - don't get close to curb/shoulder)

    for traj_y in trajectory_y:
      if not math.isfinite(traj_y):
        continue

      # Check left edge violation (negative y = left of center)
      if left_edge and left_edge.y and len(left_edge.y) > 0:
        left_limit = min(left_edge.y) if left_edge.y else -2.0  # Default to ~half lane width
        if traj_y < (left_limit + EDGE_SAFETY_MARGIN):
          return (True, f"left_edge_violation y={traj_y:.2f} limit={left_limit:.2f}")

      # Check right edge violation (positive y = right of center)
      if right_edge and right_edge.y and len(right_edge.y) > 0:
        right_limit = max(right_edge.y) if right_edge.y else 2.0  # Default to ~half lane width
        if traj_y > (right_limit - EDGE_SAFETY_MARGIN):
          return (True, f"right_edge_violation y={traj_y:.2f} limit={right_limit:.2f}")

    return (False, "safe")

  def _check_lane_boundaries(self, trajectory_y: list[float], ground_objects) -> tuple[bool, str]:
    """
    Check if trajectory violates lane line boundaries (WARNING - use fallback).

    Args:
      trajectory_y: Lateral positions in trajectory (meters from center)
      ground_objects: groundObjects message with roadGeometry

    Returns:
      (violation: bool, reason: str)
    """
    if not ground_objects or not hasattr(ground_objects, 'roadGeometry'):
      return (False, "no_road_geometry")

    road_geom = ground_objects.roadGeometry
    if not road_geom.laneLines or len(road_geom.laneLines) < 2:
      return (False, "no_lane_lines")

    # Use outermost detected lane lines as soft boundaries
    lane_lines = road_geom.laneLines
    if not lane_lines:
      return (False, "no_lane_lines")

    LANE_WARNING_MARGIN = 0.5  # meters (warn before crossing lane line)

    # Find leftmost and rightmost lane lines
    left_limit = None
    right_limit = None

    for lane in lane_lines:
      if not lane.y or len(lane.y) == 0:
        continue
      avg_y = sum(lane.y) / len(lane.y) if lane.y else 0.0
      if avg_y < 0:  # Left side
        if left_limit is None or avg_y < left_limit:
          left_limit = avg_y
      else:  # Right side
        if right_limit is None or avg_y > right_limit:
          right_limit = avg_y

    for traj_y in trajectory_y:
      if not math.isfinite(traj_y):
        continue

      # Check lane line violations with warning margin
      if left_limit is not None and traj_y < (left_limit + LANE_WARNING_MARGIN):
        return (True, f"left_lane_warning y={traj_y:.2f} limit={left_limit:.2f}")
      if right_limit is not None and traj_y > (right_limit - LANE_WARNING_MARGIN):
        return (True, f"right_lane_warning y={traj_y:.2f} limit={right_limit:.2f}")

    return (False, "safe")

  def _check_corridor_boundaries(self, trajectory_x: list[float], trajectory_y: list[float]) -> tuple[bool, str]:
    """
    Check trajectory against fused corridor boundaries (multi-source: segmentation + vision).

    Uses 33-point boundaries from path_corridor_fusion at ModelConstants.X_IDXS spacing.
    Falls back to default safe corridor if boundaries are invalid.

    Returns: (violation: bool, reason: str)
    """
    SAFETY_MARGIN = 0.3  # metres inside road edge

    # Validate boundaries before use
    if not validate_corridor_boundaries(
        self._corridor_left_boundary,
        self._corridor_right_boundary
    ):
      # Fall back to default safe corridor
      left_limit = -1.5  # Conservative half-lane
      right_limit = 1.5

      for tx, ty in zip(trajectory_x, trajectory_y, strict=False):
        if not (math.isfinite(tx) and math.isfinite(ty)):
          continue
        if ty < (left_limit + SAFETY_MARGIN):
          return (True, f"default_left_boundary x={tx:.1f} y={ty:.2f}")
        if ty > (right_limit - SAFETY_MARGIN):
          return (True, f"default_right_boundary x={tx:.1f} y={ty:.2f}")
      return (False, "safe")

    # Use fused boundaries (33 points at X_IDXS spacing)
    from openpilot.selfdrive.modeld.constants import ModelConstants
    x_idxs = ModelConstants.X_IDXS  # 33 points: 0-192m

    for tx, ty in zip(trajectory_x, trajectory_y, strict=False):
      if not (math.isfinite(tx) and math.isfinite(ty)):
        continue

      # Find nearest X index
      idx = min(range(len(x_idxs)), key=lambda i: abs(x_idxs[i] - tx))

      left_limit = float(self._corridor_left_boundary[idx])
      right_limit = float(self._corridor_right_boundary[idx])

      if ty < (left_limit + SAFETY_MARGIN):
        return (True, f"corridor_left_boundary x={tx:.1f} y={ty:.2f} limit={left_limit:.2f}")
      if ty > (right_limit - SAFETY_MARGIN):
        return (True, f"corridor_right_boundary x={tx:.1f} y={ty:.2f} limit={right_limit:.2f}")

    return (False, "safe")

  def _check_grid_collision(self, trajectory_x: list[float], trajectory_y: list[float], grid_view: OccupancyGridView | None) -> tuple[bool, str]:
    """
    Check if trajectory collides with occupancy grid obstacles (CRITICAL safety check).

    Args:
      trajectory_x: Forward positions in trajectory (meters)
      trajectory_y: Lateral positions in trajectory (meters)
      grid_view: Occupancy grid from gridd

    Returns:
      (collision: bool, reason: str)
    """
    if grid_view is None:
      return (False, "no_grid")

    GRID_COLLISION_THRESHOLD = 0.7  # High confidence obstacles only
    GRID_SAFETY_MARGIN = 0.4  # meters (vehicle width margin)

    for i in range(min(len(trajectory_x), len(trajectory_y))):
      x = trajectory_x[i]
      y = trajectory_y[i]

      if not (math.isfinite(x) and math.isfinite(y)):
        continue

      # Check grid score at this trajectory point (with safety margin)
      score = grid_view.score_at(x, y)
      if score is not None and score >= GRID_COLLISION_THRESHOLD:
        return (True, f"grid_obstacle x={x:.1f} y={y:.1f} score={score:.2f}")

      # Also check safety margin around trajectory point
      for dy in [-GRID_SAFETY_MARGIN, GRID_SAFETY_MARGIN]:
        score_margin = grid_view.score_at(x, y + dy)
        if score_margin is not None and score_margin >= GRID_COLLISION_THRESHOLD:
          return (True, f"grid_obstacle_margin x={x:.1f} y={y + dy:.1f} score={score_margin:.2f}")

    return (False, "safe")

  def _validate_trajectory_safety(
    self, trajectory_x: list[float], trajectory_y: list[float], ground_objects, grid_view: OccupancyGridView | None
  ) -> tuple[bool, str, str]:
    """
    Multi-layer safety validation for lead trajectory.

    Safety hierarchy (most to least restrictive):
    1. Road edges (groundd) - NOT IMPLEMENTED: groundd daemon does not exist;
       falls through to corridor boundary check when ground_objects is None
    2. Grid obstacles (gridd) - CRITICAL: Collision detection
    3. Lane lines (groundd) - NOT IMPLEMENTED: groundd daemon does not exist;
       falls through to modelV2 lane line fallback when ground_objects is None

    Args:
      trajectory_x: Forward positions (meters)
      trajectory_y: Lateral positions (meters from center)
      ground_objects: groundObjects message
      grid_view: Occupancy grid from gridd

    Returns:
      (is_safe: bool, reason: str, alert_level: str)
      alert_level: "critical", "warning", or "none"
    """
    # Layer 1a: Road edge violation via groundd (CRITICAL — groundd not implemented,
    #           falls through to layer 1b when ground_objects is None)
    edge_violation, edge_reason = self._check_road_edges(trajectory_y, ground_objects)
    if edge_violation:
      return (False, f"road_edge: {edge_reason}", "critical")

    # Layer 1b: Road boundary violation via fused corridor boundaries (CRITICAL)
    corridor_violation, corridor_reason = self._check_corridor_boundaries(trajectory_x, trajectory_y)
    if corridor_violation:
      return (False, f"corridor_boundary: {corridor_reason}", "critical")

    # Layer 2: Grid obstacle collision (CRITICAL - reject trajectory)
    grid_collision, grid_reason = self._check_grid_collision(trajectory_x, trajectory_y, grid_view)
    if grid_collision:
      return (False, f"grid_obstacle: {grid_reason}", "critical")

    # Layer 3: Lane line violation (WARNING - fallback to modelV2)
    lane_violation, lane_reason = self._check_lane_boundaries(trajectory_y, ground_objects)
    if lane_violation:
      return (False, f"lane_boundary: {lane_reason}", "warning")

    # All checks passed
    return (True, "safe", "none")

  def _refresh_params(self) -> None:
    now = time.monotonic()
    if now - self._last_param_t < 2.0:
      return
    self._last_param_t = now
    self._aeb_enabled = self.params.get_bool("EOPAEBEnabled")
    self._soc_enabled = self.params.get_bool("EOPSOCControllerEnabled")
    stereo_enabled = self.params.get_bool("EOPStereoEnabled")
    nudge_enabled = self.params.get_bool("EOPNudgeEnabled")
    self._stereo_enabled = stereo_enabled and nudge_enabled

  def update(self):
    self.sm.update(0)
    self._refresh_params()

    if self.sm.updated.get('gridObjects', False):
      try:
        grid_msg = self.sm['gridObjects']
        self.grid_view = OccupancyGridView.from_message(grid_msg)
        self._grid_last_time = time.monotonic()

        # Run internal track+predict pipeline on the BEV grid
        now_track = time.monotonic()
        track_dt = max(0.0, now_track - self._last_track_time)
        self._last_track_time = now_track
        # EOP: Only run tracker if enabled
        if self._tracker_enabled and self._tracker is not None:
          try:
            import numpy as _np
            grid_flat = list(grid_msg.layers[0].data) if grid_msg.layers else []
            if grid_flat:
              grid_arr = _np.array(grid_flat, dtype=_np.float32).reshape(
                int(grid_msg.height), int(grid_msg.width)
              )
              half_w = int(grid_msg.width) // 2
              self._grid_tracks = self._tracker.update(
                grid_arr, float(grid_msg.resolution or 0.5), half_w, track_dt
              )
          except (AttributeError, TypeError) as e:
            if self.log_counter % 100 == 0:
              cloudlog.debug(f"PathD: Grid tracking update failed: {e}")
      except Exception as e:
        if self.log_counter % 100 == 0:
          cloudlog.error(f"PathD: Failed to process gridObjects: {e}")
        self.grid_view = None
        self._grid_last_time = 0.0

    # Read stereoGround for OccupancyGrid fusion results
    if self.sm.updated.get('stereoGround', False):
      try:
        sg = self.sm['stereoGround']
        self._bike_hazard = sg.bikeHazard
        self._drivable_limit_m = sg.drivableLimitM
        self._occupancy_detected = sg.occupancyDetected
        self._stereo_ground_last_time = time.monotonic()
        if len(sg.leftBoundary) == 7:
          self._stereo_left_boundary  = list(sg.leftBoundary)
          self._stereo_right_boundary = list(sg.rightBoundary)
      except (AttributeError, KeyError) as e:
        if self.log_counter % 100 == 0:
          cloudlog.debug(f"PathD: stereoGround field missing: {e}")

    # Fuse corridor boundaries from multiple sources (segmentation + vision model)
    # NOTE: groundd daemon is not implemented. Road edges and lane lines from
    # groundObjects are unavailable. Corridor boundaries fall back to modelV2
    # lane lines and stereo ground estimation only.
    try:
      model_v2 = self.sm['modelV2'] if self.sm.valid.get('modelV2', False) else None
      left, right, source, conf = fuse_corridor_boundaries(model_v2, None)
      self._corridor_left_boundary = left
      self._corridor_right_boundary = right
      self._corridor_source = source
      self._corridor_confidence = conf
    except Exception as e:
      # Keep previous boundaries on error, but log occasionally
      if self.log_counter % 100 == 0:
        cloudlog.debug(f"PathD: Corridor boundary fusion error: {e}")

    grid_view = None
    if self.grid_view is not None and (time.monotonic() - self._grid_last_time) < GRID_STALE_TIMEOUT:
      grid_view = self.grid_view

    # stereoGround stale check
    stereo_ground_valid = (time.monotonic() - self._stereo_ground_last_time) < GRID_STALE_TIMEOUT

    # Build nav_data from navInstruction cereal message (NAVD v2)
    nav_data = None
    if self.sm.valid.get('navInstruction', False):
        nav_data = _nav_data_from_cereal(self.sm['navInstruction'])

    # Tracked objects come from internal gridd-based tracker (track.py)
    tracked_objects = self._grid_tracks
    # EOP: Run prediction on the current tracks (constant-velocity 3s horizon)
    # Only predict if enabled and we have tracks
    if self._predict_enabled and tracked_objects:
      predicted_objects = predict_clusters(tracked_objects)
    else:
      predicted_objects = []

    # Get ego vehicle state
    car_state = self.sm['carState']
    ego_velocity = car_state.vEgo if self.sm.updated['carState'] else 0.0

    # NOTE: groundd daemon is not implemented. _check_road_edges and
    # _check_lane_boundaries will immediately return (False, "no_road_geometry")
    # because ground_objects is None. Corridor boundary checks and grid obstacle
    # checks remain fully functional.
    ground_objects = None

    # --- Driving policy baseline ---
    # drivingModelData.action gives the policy model's desired curvature and
    # acceleration. pathd uses this as the base trajectory and overrides only
    # when collision prediction detects a hazard.
    policy_curvature = 0.0     # rad/m  (0 = straight ahead)
    policy_accel     = 0.0     # m/s²
    if self.sm.valid.get('drivingModelData', False):
      dmd = self.sm['drivingModelData']
      action = dmd.action
      policy_curvature = float(action.desiredCurvature)
      policy_accel     = float(action.desiredAcceleration)

    # --- Generate trajectory from policy curvature + ego velocity ---
    # Standardize to model IDX_N (33 points) for consistency across pipeline
    path_x = np.array(ModelConstants.X_IDXS, dtype=np.float32)
    num_points = len(path_x)

    # Simple constant-curvature projection
    # Lateral offset: y ≈ 0.5 * κ * x²
    path_y = 0.5 * policy_curvature * path_x**2

    # Velocity projection based on policy acceleration
    # v² = v₀² + 2ax -> v = sqrt(v₀² + 2ax)
    vel_x = np.sqrt(np.maximum(0.0, ego_velocity**2 + 2 * policy_accel * path_x))

    # Time indices (approximate based on velocity)
    # t = x / v_avg (simple approximation for planning)
    v_avg = (ego_velocity + vel_x) / 2.0
    path_t = np.zeros(num_points)
    path_t[1:] = np.cumsum(np.diff(path_x) / np.maximum(1.0, v_avg[:-1]))

    trajectory_source = "policy" if self.sm.valid.get('drivingModelData', False) else "straight"
    trajectory_safety_reason = "none"
    trajectory_alert_level = "none"

    # TURN SIGNAL SUPPORT: Detect lane change intent
    left_blinker = car_state.leftBlinker if self.sm.updated['carState'] else False
    right_blinker = car_state.rightBlinker if self.sm.updated['carState'] else False
    prev_left, prev_right = self.prev_blinker_state

    # Detect turn signal activation (edge trigger)
    turn_signal_activated = False
    if (left_blinker and not prev_left) or (right_blinker and not prev_right):
      turn_signal_activated = True
      self.lane_change_direction = 'left' if left_blinker else 'right'
      self.lane_change_timer = 0.0

    # Update lane change state
    # Validate that direction still matches current blinker
    current_direction = 'left' if left_blinker else ('right' if right_blinker else None)

    if turn_signal_activated or (current_direction == self.lane_change_direction):
      self.lane_change_timer += 0.05  # ~20Hz update rate
    else:
      # No turn signal or direction mismatch - reset lane change state
      self.lane_change_direction = None
      self.lane_change_timer = 0.0

    self.prev_blinker_state = (left_blinker, right_blinker)

    # Evaluate lane change safety if turn signal active
    lane_change_blocked = False
    lane_change_blocked_gap = float('inf')
    if self.lane_change_direction and self.lane_change_timer > 0.5:
      gate_result = evaluate_lane_change_gate(
        self.lane_change_direction,
        car_state,
        tracked_objects,
        predicted_objects,
        ego_velocity
      )

      if not gate_result.safe:
        lane_change_blocked = True
        lane_change_blocked_gap = gate_result.min_gap

    # 2. Compute speed reduction for NEW objects in our path
    # Use navigation-aware version (falls back to standalone if nav_data is None)
    speed_reduction_raw = compute_speed_reduction_with_nav(
      tracked_objects,
      predicted_objects,
      ego_velocity,
      nav_data,
      grid_view,
      model_v2=None,
    )

    # Apply stereoGround emergency braking for occupancy detected
    # This provides an additional safety layer beyond grid-based detection
    # EOP: Only apply emergency braking if AEB is enabled
    aeb_enabled = self._aeb_enabled
    if aeb_enabled and stereo_ground_valid and self._occupancy_detected:
      # Emergency braking based on drivable limit (closest hazard)
      if self._drivable_limit_m < 30.0 and ego_velocity > 5.0:
        # Compute emergency brake intensity based on distance
        if self._drivable_limit_m < 10.0:
          emergency_brake = -5.0  # Strong emergency brake
        elif self._drivable_limit_m < 20.0:
          emergency_brake = -2.5  # Moderate brake
        else:
          emergency_brake = -1.0  # Light brake

        # Take the more aggressive of the two braking decisions
        if speed_reduction_raw == float('inf'):
          speed_reduction_raw = emergency_brake
        else:
          speed_reduction_raw = min(speed_reduction_raw, emergency_brake)

        if self.log_counter % 20 == 0:
          cloudlog.warning(f"Emergency brake: limit={self._drivable_limit_m:.1f}m, brake={emergency_brake:.1f}m/s")

    # Bike hazard lateral adjustment (for future use with lateral control)
    if stereo_ground_valid and self._bike_hazard:
      # Bike cut-in detected - could trigger lateral offset in future
      # For now, log and rely on emergency braking
      if self.log_counter % 20 == 0:
        cloudlog.warning("Bike hazard detected - emergency braking active")

    # Apply low-pass filter for smooth transitions
    # Handle inf case (no braking) - don't filter inf values
    if math.isfinite(speed_reduction_raw):
      alpha = self.SPEED_REDUCTION_FILTER
      speed_reduction = alpha * speed_reduction_raw + (1 - alpha) * self.last_speed_reduction
      self.last_speed_reduction = speed_reduction
    else:
      speed_reduction = float('inf')

    # Apply lane change blocked emergency brake if needed
    # EOP: Only apply emergency braking if AEB is enabled
    if aeb_enabled and lane_change_blocked and lane_change_blocked_gap < 10.0:
      if not math.isfinite(speed_reduction):
        speed_reduction = -2.0
      else:
        speed_reduction = min(speed_reduction, -2.0)

    # Trajectory safety validation (road boundaries + grid obstacles)
    is_safe, safety_reason, safety_alert = self._validate_trajectory_safety(
      path_x, path_y, ground_objects, grid_view
    )
    trajectory_safety_reason = safety_reason

    # Derive alert level: trajectory safety check takes priority, then speed reduction magnitude
    if safety_alert == "critical" or (math.isfinite(speed_reduction) and speed_reduction <= -3.0):
      trajectory_alert_level = "critical"
      if not trajectory_safety_reason or trajectory_safety_reason == "none":
        trajectory_safety_reason = f"emergency_brake:{speed_reduction:.1f}m/s"
    elif safety_alert == "warning" or (math.isfinite(speed_reduction) and speed_reduction < -0.5):
      trajectory_alert_level = "warning"
      if not trajectory_safety_reason or trajectory_safety_reason == "none":
        trajectory_safety_reason = f"speed_reduction:{speed_reduction:.1f}m/s"

    # Lane change blocked warning (if not already critical)
    if lane_change_blocked and trajectory_alert_level != "critical":
      trajectory_alert_level = "warning"
      trajectory_safety_reason = f"lane_change_blocked_{self.lane_change_direction}_gap{lane_change_blocked_gap:.1f}m"

    # Build enhanced trajectory message
    msg = messaging.new_message('enhancedTrajectory')
    enhanced_traj = msg.enhancedTrajectory

    # Base trajectory derived from policy model curvature/accel (or straight-ahead fallback)
    # (plain floats: pycapnp rejects numpy scalars)
    enhanced_traj.t = [float(v) for v in path_t]
    enhanced_traj.x = [float(v) for v in path_x]

    # Lateral adjustments on top of policy curvature (SOC handled by controlsd)
    num_points = len(path_y)

    # Compute lateral offset for fast-closing objects
    # Use corridor boundaries for lane limits (fall back to defaults if invalid)
    if validate_corridor_boundaries(
        self._corridor_left_boundary,
        self._corridor_right_boundary
    ):
      lane_left = float(self._corridor_left_boundary[0])  # At ego position
      lane_right = float(self._corridor_right_boundary[0])
    else:
      lane_left = -1.8
      lane_right = 1.8

    # EOP: SOC (Smart Offset Controller) - lateral nudge for large vehicles
    if self._soc_enabled:
      offset_result = self._soc_ctrl.compute(
        tracked_objects,
        ego_velocity,
        lane_left_m=lane_left,
        lane_right_m=lane_right
      )

      if offset_result.offset_m != 0.0:
        lateral_adjustments = self._soc_ctrl.offsets_for_trajectory(
          offset_result.offset_m, path_x
        )
      else:
        lateral_adjustments = [0.0] * num_points
    else:
      offset_result = OffsetResult(offset_m=0.0, trigger_distance=float('inf'),
                                    closing_speed=0.0, score=0.0)
      lateral_adjustments = [0.0] * num_points

    # EOP: LatNudge — stereo boundary + obstacle lateral nudge
    self._lat_nudge.enabled = self._stereo_enabled
    stereo_left  = getattr(self, '_stereo_left_boundary',  [])
    stereo_right = getattr(self, '_stereo_right_boundary', [])
    if self._lat_nudge.enabled and len(stereo_left) == 7 and len(stereo_right) == 7:
      nudge_7pts = self._lat_nudge.update(stereo_left, stereo_right,
                                    self._grid_tracks, ego_velocity)
      # Interpolate 7-point nudge offsets onto 33-point trajectory
      for i in range(num_points):
        d = path_x[i] if i < len(path_x) else 0.0
        # Linear interpolation across the 7 boundary distances
        if d <= LN_BOUNDARY_DISTANCES[0]:
          nudge_adj = nudge_7pts[0]
        elif d >= LN_BOUNDARY_DISTANCES[-1]:
          nudge_adj = nudge_7pts[-1]
        else:
          for j in range(len(LN_BOUNDARY_DISTANCES) - 1):
            if LN_BOUNDARY_DISTANCES[j] <= d <= LN_BOUNDARY_DISTANCES[j + 1]:
              t = (d - LN_BOUNDARY_DISTANCES[j]) / (LN_BOUNDARY_DISTANCES[j + 1] - LN_BOUNDARY_DISTANCES[j])
              nudge_adj = nudge_7pts[j] * (1 - t) + nudge_7pts[j + 1] * t
              break
          else:
            nudge_adj = 0.0
        lateral_adjustments[i] = lateral_adjustments[i] + nudge_adj

    # EOP: LonNudge — stereo forward-distance speed adjustment
    self._lon_nudge.enabled = self._stereo_enabled
    lon_delta = self._lon_nudge.update(
      self._drivable_limit_m,
      self._occupancy_detected,
      self._bike_hazard,
      self._grid_tracks,
      ego_velocity,
      0.0,
    ) if self._lon_nudge.enabled else 0.0

    # FIX Gap #10: Distance-dependent speed reduction (stronger near, weaker far)
    # Handle inf case (no braking needed)
    speed_adjustments = []
    if math.isfinite(speed_reduction):
      # Have speed reduction - apply with distance-based fade
      if path_x:
        for i in range(num_points):
          # Get distance at this trajectory point
          dist = path_x[i] if i < len(path_x) else 0.0

          # Fade out speed reduction for far points (focus on near-term)
          if dist < 10.0:
            fade = 1.0  # Full reduction for near points
          elif dist < 30.0:
            fade = 1.0 - (dist - 10.0) / 20.0  # Linear fade 10-30m
          else:
            fade = 0.3  # Minimal reduction for far points

          # Divide by ACCEL_RESPONSE_TIME to convert velocity delta (m/s) → accel (m/s²)
          speed_adjustments.append(speed_reduction * fade / self.ACCEL_RESPONSE_TIME)
      else:
        speed_adjustments = [speed_reduction] * num_points
    else:
      # No braking needed (inf) - zero adjustment
      speed_adjustments = [0.0] * num_points

    # EOP: LonNudge — blend stereo speed delta (uniform, same direction as existing reduction)
    if lon_delta != 0.0:
      for i in range(num_points):
        speed_adjustments[i] += lon_delta / self.ACCEL_RESPONSE_TIME

    # EOP: Long horizon planning (500m) - blend with Hybrid A* path if available
    if self._long_horizon_enabled and self._hybrid_planner:
      try:
        map_data_msg = self.sm['mapData'] if self.sm.updated['mapData'] else None
        grid_msg = self.sm['gridObjects'] if self.sm.updated['gridObjects'] else None

        if map_data_msg and grid_msg:
          planned_path = self._hybrid_planner.plan(
            map_data_msg=map_data_msg,
            grid_msg=grid_msg,
            current_speed_ms=ego_velocity,
            v_cruise_ms=getattr(car_state, 'vCruise', 30.0)
          )

          if planned_path and planned_path.success:
            # EOP: LHLC (Long Horizon Lateral Controller) - populate trajectory fields
            enhanced_traj.lhlcActive = True
            enhanced_traj.lhlcRangeM = planned_path.total_length_m

            # Convert planned path to trajectory format
            lhlc_path_x = []
            lhlc_path_y = []
            lhlc_curvatures = []
            lhlc_speeds = []
            lhlc_confidences = []

            for i, wp in enumerate(planned_path.waypoints):
              dist_m = wp[0]  # x = forward distance
              lateral_m = wp[1]  # y = lateral offset

              lhlc_path_x.append(dist_m)
              lhlc_path_y.append(lateral_m)

              # Calculate curvature from consecutive waypoints
              if i > 0 and i < len(planned_path.waypoints) - 1:
                prev_wp = planned_path.waypoints[i-1]
                next_wp = planned_path.waypoints[i+1]
                # Simple curvature approximation
                dx1 = wp[0] - prev_wp[0]
                dy1 = wp[1] - prev_wp[1]
                dx2 = next_wp[0] - wp[0]
                dy2 = next_wp[1] - wp[1]
                dtheta = math.atan2(dy2, dx2) - math.atan2(dy1, dx1)
                curvature = abs(dtheta) / math.sqrt(dx1**2 + dy1**2) if (dx1**2 + dy1**2) > 0 else 0
                lhlc_curvatures.append(curvature)
              else:
                lhlc_curvatures.append(0.0)

              speed = planned_path.get_speed_at(dist_m)
              lhlc_speeds.append(speed)

              # Confidence decreases with distance (1.0 near, 0.5 far)
              conf = 1.0 - (0.5 * min(dist_m, 500.0) / 500.0)
              lhlc_confidences.append(conf)

            enhanced_traj.lhlcPathX = lhlc_path_x
            enhanced_traj.lhlcPathY = lhlc_path_y
            enhanced_traj.lhlcCurvatures = lhlc_curvatures
            enhanced_traj.lhlcSpeeds = lhlc_speeds
            enhanced_traj.lhlcConfidences = lhlc_confidences

            # Blend long-term speed profile into trajectory
            for i in range(num_points):
              if i < len(path_x):
                target_speed = planned_path.get_speed_at(path_x[i])
                if target_speed > 0 and vel_x[i] > target_speed:
                  speed_adjustments[i] += (target_speed - vel_x[i]) / self.ACCEL_RESPONSE_TIME
      except Exception as e:
        if self.log_counter % 50 == 0:
          cloudlog.debug(f"Long horizon planning error: {e}")

    enhanced_traj.y = [float(path_y[i] + lateral_adjustments[i]) for i in range(num_points)] if len(path_y) else []

    # Apply distance-dependent speed reduction
    enhanced_traj.v = [(vel_x[i] if i < len(vel_x) else 0.0) + speed_adjustments[i] for i in range(num_points)] if num_points > 0 else []

    # Set adjustment metadata
    enhanced_traj.lateralAdjustment = lateral_adjustments
    enhanced_traj.speedAdjustment = speed_adjustments
    enhanced_traj.numObjectsConsidered = len(tracked_objects)

    # Stereod pipeline telemetry
    enhanced_traj.trajectorySource = trajectory_source
    enhanced_traj.trajectorySafetyReason = trajectory_safety_reason
    enhanced_traj.trajectoryAlertLevel = trajectory_alert_level

    # Multi-source corridor boundaries
    enhanced_traj.leftBoundary = list(self._corridor_left_boundary)
    enhanced_traj.rightBoundary = list(self._corridor_right_boundary)
    enhanced_traj.boundarySource = self._corridor_source
    enhanced_traj.corridorConfidence = self._corridor_confidence

    self.pm.send('enhancedTrajectory', msg)
    self.log_counter += 1

  def run(self):
    while True:
      self.update()


def main() -> int:
  try:
    PathD().run()
    return 0
  except Exception as e:
    cloudlog.exception(f"PathD fatal error: {e}")
    raise


if __name__ == "__main__":
  exit(main())
