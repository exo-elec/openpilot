"""
dlat.py - Dynamic Lateral Profile (DLAT)

Intelligent arbitration between rule-based lane following (Laneful) and
End-to-End (E2E) mapless navigation (Laneless).

Operates inside controlsd to select the optimal lateral control mode based on
real-time lane line confidence and model predictions.
"""

import time
import numpy as np
from enum import Enum
from openpilot.common.params import Params


class DLATMode(Enum):
  """DLAT operating modes."""
  laneful = 0    # Follow lane lines
  laneless = 1   # Follow predicted path (E2E)
  auto = 2       # Automatic switching based on confidence


class DLATState(Enum):
  """Internal state machine states for Auto mode."""
  laneful = 0
  evaluate = 1
  laneless = 2


# Hysteresis thresholds
LANEFUL_TO_LANELESS_THRESH = 0.4   # Confidence below this triggers evaluation
LANELESS_TO_LANEFUL_THRESH = 0.7   # Confidence above this returns to laneful
LANEFUL_TO_LANELESS_TIME = 1.0     # Seconds below threshold before switching
LANELESS_TO_LANEFUL_TIME = 2.0     # Seconds above threshold before returning
PATH_DEVIATION_THRESH = 1.5        # Meters of deviation to force laneless
MODEL_CONFIDENCE_THRESH = 0.8      # Minimum model confidence for path deviation check


class DLAT:
  """
  Dynamic Lateral Profile controller.

  Manages switching between Laneful (lane line following) and Laneless
  (E2E path following) modes based on real-time confidence metrics.
  """

  PARAM_REFRESH_S = 2.0

  def __init__(self):
    self.params = Params()
    self.mode = DLATMode.auto
    self.state = DLATState.laneful
    self.prev_state = DLATState.laneful

    # Timers for hysteresis
    self.state_entry_time = time.monotonic()
    self.low_confidence_start = 0.0
    self._last_param_t = 0.0

    # Cached values
    self.lane_confidence = 1.0
    self.model_confidence = 1.0
    self.use_laneless = False
    self._curve_assist_enabled = True

    self._load_params()

  def _load_params(self):
    """Load DLAT parameters (call periodically, not every frame).

    Mode is always `auto` (automatic Laneful/Laneless arbitration) -- a
    default, standard behavior of this branch, not a user-selectable
    choice. There is no param or panel control that forces a fixed
    laneful or laneless mode, matching dev/NGP10's design.
    """
    now = time.monotonic()
    if now - self._last_param_t < self.PARAM_REFRESH_S:
      return
    self._last_param_t = now
    self.mode = DLATMode.auto
    try:
      self._curve_assist_enabled = self.params.get_bool("EOPDLPCurvesEnabled")
    except (ValueError, TypeError):
      self._curve_assist_enabled = True

  @staticmethod
  def calculate_lane_confidence(model_v2):
    """
    Calculate lane line confidence from modelV2.

    Weights inner lines higher than outer lines. Static and dependency-free
    so other modules (e.g. desire_helper.py's LCA initiation gate) can reuse
    this exact formula without instantiating a full DLAT (which polls params
    and carries hysteresis state meant for the Laneful/Laneless arbiter, not
    a one-shot check).
    """
    if not model_v2 or not hasattr(model_v2, 'laneLineProbs'):
      return 0.5

    lane_probs = model_v2.laneLineProbs
    if len(lane_probs) < 4:
      return 0.5

    # Weight: [outer_left, inner_left, inner_right, outer_right]
    weights = [0.1, 0.4, 0.4, 0.1]
    confidence = sum(p * w for p, w in zip(lane_probs, weights, strict=False))

    return np.clip(confidence, 0.0, 1.0)

  def _calculate_model_confidence(self, model_v2):
    """
    Calculate overall model confidence for path prediction.
    """
    # Start with lane confidence as base
    lane_conf = self.calculate_lane_confidence(model_v2)

    # Path prediction confidence (inverse of std dev)
    path_confidence = 0.5
    if hasattr(model_v2, 'predictedPathStd') and model_v2.predictedPathStd:
      try:
        path_std = np.mean(model_v2.predictedPathStd)
        path_confidence = 1.0 / (1.0 + path_std)
      except (ValueError, TypeError):
        path_confidence = 0.5

    # Combined confidence
    model_confidence = 0.6 * lane_conf + 0.4 * path_confidence

    return np.clip(model_confidence, 0.0, 1.0)

  def _calculate_path_deviation(self, model_v2):
    """
    Calculate lateral deviation between laneful and laneless paths.

    Returns deviation in meters.
    """
    if not model_v2:
      return 0.0

    # Get laneful reference (left inner lane line)
    laneful_path = None
    if hasattr(model_v2, 'laneLines') and len(model_v2.laneLines) > 1:
      laneful_path = model_v2.laneLines[1]  # Left inner

    # Get laneless path
    laneless_path = None
    if hasattr(model_v2, 'predictedPath'):
      laneless_path = model_v2.predictedPath

    if laneful_path is None or laneless_path is None:
      return 0.0

    # Calculate deviation at 10m lookahead
    try:
      if (hasattr(laneful_path, 'y') and hasattr(laneless_path, 'y') and
          len(laneful_path.y) > 5 and len(laneless_path.y) > 5):
        # Use point at index 5 (approximately 10m lookahead)
        deviation = abs(laneless_path.y[5] - laneful_path.y[5])
        return deviation
    except (IndexError, AttributeError):
      pass

    return 0.0

  def _predict_curve(self, model_v2, car_state):
    """
    Predict if there's a curve ahead that warrants laneless mode.

    Uses orientation rate from modelV2 to detect upcoming curves.
    Returns True if curve detected and EOPDLPCurvesEnabled is set.
    """
    if not self._curve_assist_enabled:
      return False

    if not model_v2 or not hasattr(model_v2, 'orientationRate'):
      return False

    z_rates = model_v2.orientationRate.z
    if len(z_rates) == 0:
      return False

    # Use rate at ~2s lookahead (index 4 at 20Hz)
    rate_2s = abs(z_rates[min(4, len(z_rates) - 1)])
    speed = max(car_state.vEgo, 1.0)

    # Curvature = yaw_rate / speed
    curvature = rate_2s / speed

    CURVE_THRESHOLD = 0.055  # 1/m - threshold for curve detection
    return curvature > CURVE_THRESHOLD

  def _update_auto_state(self, lane_confidence, model_confidence, path_deviation, curve_detected=False):
    """
    Update Auto mode state machine with hysteresis.
    """
    current_time = time.monotonic()

    # Check for forced laneless due to path deviation or curve ahead
    force_laneless = ((path_deviation > PATH_DEVIATION_THRESH and
                      model_confidence > MODEL_CONFIDENCE_THRESH) or
                      curve_detected)

    if self.state == DLATState.laneful:
      # Check if we should start evaluating laneless
      if force_laneless:
        # Immediate switch for large deviation with high confidence
        self.state = DLATState.laneless
        self.state_entry_time = current_time
      elif lane_confidence < LANEFUL_TO_LANELESS_THRESH:
        if self.low_confidence_start == 0.0:
          self.low_confidence_start = current_time
        elif current_time - self.low_confidence_start >= LANEFUL_TO_LANELESS_TIME:
          # Transition to evaluation state
          self.state = DLATState.evaluate
          self.state_entry_time = current_time
      else:
        self.low_confidence_start = 0.0

    elif self.state == DLATState.evaluate:
      # Check if confidence recovered
      if lane_confidence >= LANELESS_TO_LANEFUL_THRESH:
        self.state = DLATState.laneful
        self.state_entry_time = current_time
        self.low_confidence_start = 0.0
      elif force_laneless:
        # Force immediate switch
        self.state = DLATState.laneless
        self.state_entry_time = current_time
      elif current_time - self.state_entry_time >= 1.0:  # 1s additional in evaluate
        # Transition to laneless
        self.state = DLATState.laneless
        self.state_entry_time = current_time

    elif self.state == DLATState.laneless:
      # Check if we should return to laneful
      if lane_confidence > LANELESS_TO_LANEFUL_THRESH:
        if current_time - self.state_entry_time >= LANELESS_TO_LANEFUL_TIME:
          self.state = DLATState.laneful
          self.state_entry_time = current_time
          self.low_confidence_start = 0.0

  def update(self, model_v2, car_state=None):
    """
    Update DLAT and determine lateral control mode.

    Args:
      model_v2: modelV2 cereal message
      car_state: carState cereal message (optional, for curve detection)

    Returns:
      (use_laneless, mode_str, state_str) where:
        - use_laneless: True if laneless mode should be used
        - mode_str: Current mode name for logging
        - state_str: Current state name for logging
    """
    # Load parameters
    self._load_params()

    # Calculate confidence metrics
    self.lane_confidence = self.calculate_lane_confidence(model_v2)
    self.model_confidence = self._calculate_model_confidence(model_v2)
    path_deviation = self._calculate_path_deviation(model_v2)

    # Check for curve ahead (when car_state provided)
    curve_detected = False
    if car_state is not None:
      curve_detected = self._predict_curve(model_v2, car_state)

    # Determine output based on mode
    if self.mode == DLATMode.laneful:
      self.use_laneless = False
      mode_str = "Laneful"
      state_str = "forced"

    elif self.mode == DLATMode.laneless:
      self.use_laneless = True
      mode_str = "Laneless"
      state_str = "forced"

    else:  # Auto mode
      self._update_auto_state(self.lane_confidence, self.model_confidence, path_deviation, curve_detected)

      self.use_laneless = (self.state == DLATState.laneless)
      mode_str = "Auto"
      state_str = self.state.name

    return self.use_laneless, mode_str, state_str

  def get_debug_dict(self):
    """Return debug info as dictionary for logging."""
    return {
      "mode": self.mode.name,
      "state": self.state.name,
      "use_laneless": self.use_laneless,
      "lane_confidence": round(self.lane_confidence, 3),
      "model_confidence": round(self.model_confidence, 3),
    }
