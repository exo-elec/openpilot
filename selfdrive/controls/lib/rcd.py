"""
RCD - Road Condition Detection Controller

Classifies road surface conditions from camera imagery and applies
speed limits through the longitudinal planner.

Two detection methods:
  1. Classical CV (primary): HSV analysis of road ROI - no NPU needed
  2. Segmentation-enhanced (optional): Uses monoSegments from monod

Conditions detected:
  - GOOD: Normal dry road
  - WET: Wet surface (low saturation, low value)
  - ICY: Icy/slippery (high brightness, specular reflections)
  - SNOW: Snow covered
  - DEBRIS: Obstacles/debris on road

Integration:
  - Reads roadCameraState (or surfaceStatus from surfaced)
  - Publishes speed limit via driverAssistance message
  - Longitudinal planner applies limit similar to VTSC/MTSC/SQSC

Reference: VisionPilot Surface Analyzer (classical CV approach)
"""
from __future__ import annotations

import time
from enum import Enum
from dataclasses import dataclass
from collections import deque

import numpy as np
import cv2

from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.swaglog import cloudlog


class RoadCondition(Enum):
  """Road condition types."""
  GOOD = 0
  WET = 1
  ICY = 2
  SNOW = 3
  DEBRIS = 4


@dataclass
class RoadConditionResult:
  """Road condition detection result."""
  condition: RoadCondition
  confidence: float
  description: str
  speed_limit_ms: float   # 0.0 = no limit


@dataclass
class RCDState:
  """RCD controller state output."""
  condition: RoadCondition
  confidence: float
  speed_limit_ms: float
  is_active: bool
  reason: str


class RoadConditionClassifier:
  """
  Classify road surface conditions from camera images.

  Uses classical computer vision (no NPU required):
  - HSV color space analysis
  - Bright spot detection (specular reflections)
  - Saturation/value thresholds

  Processes bottom 40% of image (road ROI).
  """

  def __init__(
    self,
    roi_height_ratio: float = 0.4,
    icy_threshold: float = 0.1,
    wet_saturation_threshold: float = 30.0,
    wet_value_threshold: float = 80.0,
    debris_threshold: float = 0.05,
  ):
    self.roi_height_ratio = roi_height_ratio
    self.icy_threshold = icy_threshold
    self.wet_saturation_threshold = wet_saturation_threshold
    self.wet_value_threshold = wet_value_threshold
    self.debris_threshold = debris_threshold

    # Speed limits for conditions (m/s)
    self.speed_limits = {
      RoadCondition.GOOD: 0.0,      # No limit
      RoadCondition.WET: 12.0,      # ~43 km/h (EVP zone 1 ceiling)
      RoadCondition.ICY: 8.0,       # ~30 km/h
      RoadCondition.SNOW: 8.0,      # ~30 km/h
      RoadCondition.DEBRIS: 12.0,   # ~43 km/h (EVP zone 1 ceiling)
    }

  def classify(self, image: np.ndarray) -> RoadConditionResult:
    """
    Classify road condition from BGR image.

    Args:
      image: BGR image from road camera

    Returns:
      RoadConditionResult with classification
    """
    h, w = image.shape[:2]
    roi_y = int(h * (1.0 - self.roi_height_ratio))
    road_roi = image[roi_y:, :]

    # Convert to HSV
    hsv = cv2.cvtColor(road_roi, cv2.COLOR_BGR2HSV)

    # Calculate metrics
    avg_saturation = float(np.mean(hsv[:, :, 1]))
    avg_value = float(np.mean(hsv[:, :, 2]))

    # Detect bright spots (specular reflections = icy/wet)
    gray = cv2.cvtColor(road_roi, cv2.COLOR_BGR2GRAY)
    _, bright_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    bright_ratio = float(np.sum(bright_mask > 0) / (road_roi.shape[0] * road_roi.shape[1]))

    # Classify based on metrics
    if bright_ratio > self.icy_threshold and avg_value > 180:
      condition = RoadCondition.ICY
      confidence = 0.7
      description = 'icy_surface'
    elif avg_saturation < self.wet_saturation_threshold and avg_value < self.wet_value_threshold:
      condition = RoadCondition.WET
      confidence = 0.6
      description = 'wet_surface'
    elif bright_ratio > self.debris_threshold:
      condition = RoadCondition.DEBRIS
      confidence = 0.5
      description = 'debris_detected'
    else:
      condition = RoadCondition.GOOD
      confidence = 0.9
      description = 'good'

    return RoadConditionResult(
      condition=condition,
      confidence=confidence,
      description=description,
      speed_limit_ms=self.speed_limits[condition]
    )


class RCD:
  """
  Road Condition Detection controller.

  Monitors road surface quality and applies speed limits.
  Similar to VTSC/MTSC/SQSC: perception → speed limit → planner.

  Usage:
    rcd = RCD()
    state = rcd.update(sm)
    if state.is_active:
      v_cruise = min(v_cruise, state.speed_limit_ms)
  """

  # Hysteresis: require N consecutive frames before changing condition
  HYSTERESIS_FRAMES = 5

  # Confidence threshold for applying speed limit
  MIN_CONFIDENCE = 0.5

  # Smoothing filter time constant
  SPEED_FILTER_TC = 2.0  # seconds

  PARAM_REFRESH_S = 5.0

  def __init__(self):
    self.params = Params()
    self.enabled = self.params.get_bool("EOPRCDEnabled")
    self._last_param_t = 0.0
    self.classifier = RoadConditionClassifier()

    # State
    self.current_condition = RoadCondition.GOOD
    self.current_confidence = 0.0
    self._condition_history: deque = deque(maxlen=self.HYSTERESIS_FRAMES)
    self._last_condition = RoadCondition.GOOD
    self._hysteresis_count = 0

    # Speed limit smoothing
    self._speed_filter = FirstOrderFilter(0.0, self.SPEED_FILTER_TC, DT_MDL)
    self._last_limit = 0.0

    # Frame counter
    self._frame_count = 0

    cloudlog.info(f"RCD initialized: enabled={self.enabled}")

  def update(self, sm) -> RCDState:
    """
    Main update loop.

    Args:
      sm: SubMaster with roadCameraState, surfaceStatus, monoSegments

    Returns:
      RCDState with current condition and speed limit
    """
    now = time.monotonic()
    if now - self._last_param_t >= self.PARAM_REFRESH_S:
      self._last_param_t = now
      self.enabled = self.params.get_bool("EOPRCDEnabled")

    if not self.enabled:
      return RCDState(
        condition=RoadCondition.GOOD,
        confidence=0.0,
        speed_limit_ms=0.0,
        is_active=False,
        reason="RCD disabled"
      )

    self._frame_count += 1

    # Camera/surface classification path
    state = self._update_camera(sm)

    # Radar4D weather severity (rain/snow/mud from radar clutter + wiper +
    # glass contamination) — works when the camera is blind (night, glare,
    # spray), so it can tighten the limit but never loosen it.
    radar_limit, radar_severity = self._radar_weather_limit(sm)
    if radar_limit > 0.0 and (not state.is_active or radar_limit < state.speed_limit_ms):
      state.speed_limit_ms = radar_limit
      state.is_active = True
      state.reason = f"{state.reason} + radarSeverity={radar_severity}"

    return state

  # Radar4D weather severity caps (m/s): clear/light = no cap (accel gate
  # covers it), moderate ~72 km/h, heavy ~43 km/h (matches WET).
  RADAR_WEATHER_LIMITS_MS = (0.0, 0.0, 20.0, 12.0)

  def _radar_weather_limit(self, sm) -> tuple[float, int]:
    """Speed cap from radar4d weather severity (0-3).  Returns (limit, severity);
    limit 0.0 = no radar limit."""
    if not sm.valid.get('radar4d', False):
      return 0.0, 0
    severity = min(max(int(sm['radar4d'].weatherSeverity), 0), len(self.RADAR_WEATHER_LIMITS_MS) - 1)
    return self.RADAR_WEATHER_LIMITS_MS[severity], severity

  def _update_camera(self, sm) -> RCDState:
    """Camera/surface classification path (original update flow)."""
    # Try multiple data sources in priority order
    result = self._update_from_surface_status(sm)
    if result is None:
      result = self._update_from_segmentation(sm)

    if result is None:
      return RCDState(
        condition=self.current_condition,
        confidence=self.current_confidence,
        speed_limit_ms=self._last_limit,
        is_active=False,
        reason="No data source available"
      )

    # Apply hysteresis
    condition = self._apply_hysteresis(result.condition)
    self.current_condition = condition
    self.current_confidence = result.confidence

    # Calculate speed limit
    if result.confidence >= self.MIN_CONFIDENCE and condition != RoadCondition.GOOD:
      speed_limit = self.classifier.speed_limits[condition]
      self._speed_filter.update(speed_limit)
      self._last_limit = self._speed_filter.x
      is_active = True
      reason = f"{condition.name}: limit={speed_limit:.1f}m/s"
    else:
      self._speed_filter.update(0.0)
      self._last_limit = 0.0
      is_active = False
      reason = f"{condition.name}: no limit"

    return RCDState(
      condition=condition,
      confidence=result.confidence,
      speed_limit_ms=self._last_limit,
      is_active=is_active,
      reason=reason
    )

  def _update_from_surface_status(self, sm) -> RoadConditionResult | None:
    """Try to get condition from surfaced daemon (most accurate)."""
    if not sm.valid.get('surfaceStatus', False):
      return None

    surface = sm['surfaceStatus']
    if not surface.hasSurfaceQuality:
      return None

    quality = surface.surfaceQuality
    score = quality.score  # 0-1 roughness
    texture = quality.texture

    # Map surface quality to road condition
    if score > 0.6:
      condition = RoadCondition.DEBRIS
      confidence = min(score, 0.9)
      description = f"rough_surface ({texture})"
    elif score > 0.3:
      condition = RoadCondition.WET
      confidence = score
      description = f"wet_surface ({texture})"
    else:
      condition = RoadCondition.GOOD
      confidence = 1.0 - score
      description = f"good ({texture})"

    return RoadConditionResult(
      condition=condition,
      confidence=confidence,
      description=description,
      speed_limit_ms=self.classifier.speed_limits[condition]
    )

  # EOP-CLEANUP: Removed _update_from_camera() — was a TODO stub that
  # always returned None. Production implementation requires VisionIPC frame
  # decoding which is not yet integrated. Falls through to _update_from_segmentation.

  def _update_from_segmentation(self, sm) -> RoadConditionResult | None:
    """Enhance detection using monoSegments from monod."""
    if not sm.valid.get('monoSegments', False):
      return None

    mono_seg = sm['monoSegments']
    # monoSegments has hasRoad, hasEdge, hasDrivable booleans
    # Could be enhanced with actual segmentation mask data

    # For now, use this as a secondary confirmation source
    if not mono_seg.hasRoad:
      return None

    # If road is detected but drivable area is uncertain, be cautious
    if mono_seg.hasEdge and not mono_seg.hasDrivable:
      return RoadConditionResult(
        condition=RoadCondition.DEBRIS,
        confidence=0.4,
        description="edge_no_drivable",
        speed_limit_ms=self.classifier.speed_limits[RoadCondition.DEBRIS]
      )

    return None

  def _apply_hysteresis(self, new_condition: RoadCondition) -> RoadCondition:
    """Apply hysteresis to prevent rapid condition switching."""
    self._condition_history.append(new_condition)

    if len(self._condition_history) < self.HYSTERESIS_FRAMES:
      return self._last_condition

    # Check if all recent frames agree
    if all(c == new_condition for c in self._condition_history):
      if new_condition != self._last_condition:
        cloudlog.info(f"RCD condition changed: {self._last_condition.name} → {new_condition.name}")
        self._last_condition = new_condition
      return new_condition

    return self._last_condition

  def get_speed_limit(self, current_speed_ms: float) -> float:
    """
    Get applicable speed limit.

    Returns 0.0 if no limit applies (good road).
    """
    if not self.enabled or self.current_condition == RoadCondition.GOOD:
      return 0.0
    return self._last_limit


# Convenience function for longitudinal planner integration
def apply_rcd_limit(v_cruise: float, rcd_state: RCDState) -> float:
  """
  Apply RCD speed limit to cruise velocity.

  Args:
    v_cruise: Current cruise velocity (m/s)
    rcd_state: Current RCD state

  Returns:
    Limited cruise velocity
  """
  if not rcd_state.is_active or rcd_state.speed_limit_ms <= 0:
    return v_cruise
  return min(v_cruise, rcd_state.speed_limit_ms)
