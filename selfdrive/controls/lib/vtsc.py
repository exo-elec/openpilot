"""
vtsc.py - Vision Turn Speed Control (VTSC)

Proactively reduces vehicle speed before entering curves.

Priority: Learned driver speeds > Vision-based calculation

Operates in the 0-250m range.
"""

import time
import numpy as np
from enum import Enum
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL

# Import learned speed database
from openpilot.selfdrive.controls.lib.eop_utils import get_learned_speed as _common_get_learned_speed


# EOP: Per-curvature lateral accel buckets (merged from FrogPilot curve_speed_controller)
# Drivers naturally tolerate different lateral accelerations depending on curve sharpness.
# Gentle highway curves → higher lat accel; tight turns → lower lat accel.
CurvatureBucket = Enum('CurvatureBucket', [('MILD', 'mild'), ('MEDIUM', 'medium'), ('SHARP', 'sharp')])

# (kappa_min, kappa_max, bucket_name)  — kappa in 1/m
_CURVATURE_RANGES = (
  (0.000, 0.010, CurvatureBucket.MILD),    # radius > 100 m
  (0.010, 0.030, CurvatureBucket.MEDIUM),  # radius 33–100 m
  (0.030, 1.000, CurvatureBucket.SHARP),   # radius < 33 m
)

_DEFAULT_BUCKET_LAT_ACC = {
  CurvatureBucket.MILD:   2.0,
  CurvatureBucket.MEDIUM: 1.8,
  CurvatureBucket.SHARP:  1.5,
}

_BUCKET_PARAM_KEYS = {
  CurvatureBucket.MILD:   'EOPTSCTargetLatAccelMild',
  CurvatureBucket.MEDIUM: 'EOPTSCTargetLatAccelMedium',
  CurvatureBucket.SHARP:  'EOPTSCTargetLatAccelSharp',
}

def _bucket_for_kappa(kappa: float) -> CurvatureBucket:
  """Return curvature bucket for a given curvature (1/m)."""
  for k_min, k_max, bucket in _CURVATURE_RANGES:
    if k_min <= kappa < k_max:
      return bucket
  return CurvatureBucket.SHARP  # fallback for extreme curvature


class VTSCState(Enum):
  """VTSC state machine states."""
  disabled = 0
  enabled = 1
  entering = 2
  turning = 3
  leaving = 4


# Thresholds for state transitions
ENTERING_PRED_LAT_ACC_TH = 1.3  # m/s² - Enter entering state
TURNING_LAT_ACC_TH = 1.6  # m/s² - Enter turning state
LEAVING_LAT_ACC_TH = 1.3  # m/s² - Exit turning state
ENABLED_LAT_ACC_TH = 1.1  # m/s² - Return to enabled from leaving

MIN_VELOCITY = 5.0  # m/s - Minimum speed for VTSC to operate
CURVATURE_RANGE = (0.001, 0.1)  # 1/m - Valid curvature range

# Self-calibration constants
CAL_FILTER_TC       = 300.0   # 5-min time constant for slow learning
CAL_MIN_SAMPLES     = 50      # samples before calibrated value is trusted
CAL_DEFAULT_LAT_ACC = 1.8     # m/s² — initial (and fallback) target
CAL_MIN_LAT_ACC     = 1.2     # m/s² — safety floor
CAL_MAX_LAT_ACC     = 2.5     # m/s² — comfort ceiling
CAL_PARAM_KEY       = "EOPTSCTargetLatAccel"   # legacy global param (used as seed fallback)
CAL_WRITE_EVERY_S   = 30.0    # write to Params at most every 30 s


class VTSC:
  """
  Vision Turn Speed Control.
  
  Predicts curve entry and proactively reduces speed for comfortable
  lateral acceleration.
  
  Priority:
    1. Learned driver speeds from curved database (if available at current location)
    2. Vision-based curvature calculation (fallback)
  """
  
  def __init__(self):
    self.params = Params()
    self.state = VTSCState.disabled
    self.max_pred_lat_acc = 0.0
    self.current_lat_acc = 0.0
    self.v_target = 0.0

    # Learned speed tracking
    self.using_learned_speed = False
    self.learned_speed_kph = 0.0

    # Self-calibration: per-curvature-bucket lat accel learning
    self._cal_filters: dict[CurvatureBucket, FirstOrderFilter] = {}
    self._cal_samples: dict[CurvatureBucket, int] = {}
    self._cal_last_write: dict[CurvatureBucket, float] = {}
    for bucket in CurvatureBucket:
      seed = self._load_cal_seed(bucket)
      self._cal_filters[bucket] = FirstOrderFilter(seed, CAL_FILTER_TC, DT_MDL)
      self._cal_samples[bucket] = 0
      self._cal_last_write[bucket] = 0.0

    # Param cache for bucket a_comfort (avoids per-frame I/O when uncalibrated)
    self._bucket_a_comfort_cache: dict[CurvatureBucket, tuple[float, float]] = {}
    self._bucket_cache_ttl = 2.0  # seconds

    # Cached params — refresh at most once per second
    self._cached_enabled = False
    self._cached_curve_learn = False
    self._last_param_update = 0.0
    self._param_interval = 1.0
    
  def _get_cached_params(self):
    """Cache params — file I/O no more than once per second."""
    now = time.monotonic()
    if now - self._last_param_update < self._param_interval:
      return self._cached_enabled, self._cached_curve_learn
    self._last_param_update = now
    self._cached_enabled = self.params.get_bool("EOPVTSCEnabled")
    self._cached_curve_learn = self.params.get_bool("EOPCurveSpeedLearnEnabled")
    return self._cached_enabled, self._cached_curve_learn
    
  def _calculate_lateral_acceleration(self, v_ego, model_v2):
    """
    Calculate current and predicted lateral acceleration.
    
    Args:
      v_ego: Current ego velocity (m/s)
      model_v2: modelV2 cereal message
      
    Returns:
      (current_lat_acc, max_pred_lat_acc)
    """
    if not model_v2 or not hasattr(model_v2, 'orientationRate'):
      return 0.0, 0.0
    
    # Current lateral acceleration from steering
    # a_lat = v² * curvature (approximation)
    # Use orientation rate z as proxy for curvature
    if len(model_v2.orientationRate.z) > 0:
      current_rate = abs(model_v2.orientationRate.z[0])  # rad/s
      self.current_lat_acc = v_ego * current_rate
    else:
      self.current_lat_acc = 0.0
    
    # Predicted lateral acceleration from path
    # Look ahead in the path for upcoming curves
    if (len(model_v2.orientationRate.z) >= 10 and 
        len(model_v2.velocity.x) >= 10):
      
      # Calculate predicted lateral accel along path
      # a_lat = v * yaw_rate (since yaw_rate = v * curvature)
      rates = np.abs(model_v2.orientationRate.z[:10])  # rad/s
      velocities = model_v2.velocity.x[:10]  # m/s
      
      # Filter valid predictions
      valid_mask = velocities > 1.0  # Need some velocity
      if np.any(valid_mask):
        pred_lat_accels = rates[valid_mask] * velocities[valid_mask]
        # Use 97th percentile for conservative prediction
        self.max_pred_lat_acc = np.percentile(pred_lat_accels, 97)
      else:
        self.max_pred_lat_acc = 0.0
    else:
      self.max_pred_lat_acc = 0.0
    
    return self.current_lat_acc, self.max_pred_lat_acc
  
  def _update_state_machine(self, enabled, v_ego):
    """
    Update VTSC state machine based on current conditions.
    
    Args:
      enabled: Whether VTSC is enabled via parameter
      v_ego: Current ego velocity (m/s)
    """
    if not enabled or v_ego < MIN_VELOCITY:
      self.state = VTSCState.disabled
      return
    
    # State transitions
    if self.state == VTSCState.disabled:
      if enabled and v_ego >= MIN_VELOCITY:
        self.state = VTSCState.enabled
    
    elif self.state == VTSCState.enabled:
      # Check if approaching a curve
      if self.max_pred_lat_acc >= ENTERING_PRED_LAT_ACC_TH:
        self.state = VTSCState.entering
    
    elif self.state == VTSCState.entering:
      # Check if we're now in the curve
      if self.current_lat_acc >= TURNING_LAT_ACC_TH:
        self.state = VTSCState.turning
      # Or if curve passed without entering
      elif self.max_pred_lat_acc < ENTERING_PRED_LAT_ACC_TH:
        self.state = VTSCState.enabled
    
    elif self.state == VTSCState.turning:
      # Check if exiting the curve
      if self.current_lat_acc <= LEAVING_LAT_ACC_TH:
        self.state = VTSCState.leaving
    
    elif self.state == VTSCState.leaving:
      # Return to enabled when lateral accel drops enough
      if self.current_lat_acc < ENABLED_LAT_ACC_TH:
        self.state = VTSCState.enabled
  
  def _get_bucket_a_comfort(self, kappa: float) -> float:
    """Look up comfortable lateral acceleration for the given curvature bucket."""
    bucket = _bucket_for_kappa(kappa)
    if self._cal_samples[bucket] >= CAL_MIN_SAMPLES:
      return float(np.clip(self._cal_filters[bucket].x, CAL_MIN_LAT_ACC, CAL_MAX_LAT_ACC))
    # Not enough samples — use saved param or default (cached to avoid per-frame I/O)
    now = time.monotonic()
    if bucket in self._bucket_a_comfort_cache:
      cached_val, cached_ts = self._bucket_a_comfort_cache[bucket]
      if now - cached_ts < self._bucket_cache_ttl:
        return cached_val
    try:
      val = self.params.get(_BUCKET_PARAM_KEYS[bucket])
      if val:
        result = float(np.clip(float(val), CAL_MIN_LAT_ACC, CAL_MAX_LAT_ACC))
        self._bucket_a_comfort_cache[bucket] = (result, now)
        return result
    except (ValueError, TypeError):
      pass
    self._bucket_a_comfort_cache[bucket] = (_DEFAULT_BUCKET_LAT_ACC[bucket], now)
    return _DEFAULT_BUCKET_LAT_ACC[bucket]

  def _calculate_speed_target(self, v_ego):
    """
    Calculate target speed for comfortable lateral acceleration.
    Uses per-curvature-bucket a_comfort.

    Formula: v_max = sqrt(a_comfort / kappa)
    where kappa = max_pred_lat_acc / v_ego²

    Args:
      v_ego: Current ego velocity (m/s)

    Returns:
      Target velocity (m/s) or None if no limiting needed
    """
    if self.max_pred_lat_acc <= 0.1:
      return None

    # Calculate curvature from predicted lateral accel
    # a_lat = v² * kappa  =>  kappa = a_lat / v²
    kappa = self.max_pred_lat_acc / (v_ego ** 2)

    # Clamp curvature to valid range
    kappa = np.clip(kappa, CURVATURE_RANGE[0], CURVATURE_RANGE[1])

    # Look up curvature-specific comfortable lateral acceleration
    a_comfort = self._get_bucket_a_comfort(kappa)

    # Calculate max speed for this curvature
    # v_max = sqrt(a_comfort / kappa)
    v_target = np.sqrt(a_comfort / kappa)

    return v_target
  
  def _load_cal_seed(self, bucket: CurvatureBucket) -> float:
    """Read the last saved target lat accel for a curvature bucket from Params."""
    # Try bucket-specific param first
    try:
      val = self.params.get(_BUCKET_PARAM_KEYS[bucket])
      if val:
        return float(np.clip(float(val), CAL_MIN_LAT_ACC, CAL_MAX_LAT_ACC))
    except Exception:
      pass
    # Fall back to legacy global param, then default
    try:
      val = self.params.get(CAL_PARAM_KEY)
      if val:
        return float(np.clip(float(val), CAL_MIN_LAT_ACC, CAL_MAX_LAT_ACC))
    except Exception:
      pass
    return _DEFAULT_BUCKET_LAT_ACC[bucket]

  def _calibrate(self, observed_lat_acc: float, kappa: float):
    """
    Feed the self-calibration filter with the peak lateral accel the driver
    accepted without disengaging.  Buckets observations by curvature so that
    mild, medium, and sharp curves are learned independently.
    """
    if observed_lat_acc < 0.5:
      return

    bucket = _bucket_for_kappa(kappa)

    # Clamp and update slow filter for this bucket
    clamped = float(np.clip(observed_lat_acc, CAL_MIN_LAT_ACC, CAL_MAX_LAT_ACC))
    self._cal_filters[bucket].update(clamped)
    self._cal_samples[bucket] += 1

    if self._cal_samples[bucket] >= CAL_MIN_SAMPLES:
      now = time.monotonic()
      if now - self._cal_last_write[bucket] > CAL_WRITE_EVERY_S:
        self.params.put_nonblocking(_BUCKET_PARAM_KEYS[bucket], str(round(self._cal_filters[bucket].x, 3)))
        self._cal_last_write[bucket] = now

  def update(self, v_ego, model_v2, lat: float = 0.0, lon: float = 0.0):
    """
    Update VTSC and return speed limit.

    Priority: Learned speed > Vision-based calculation

    Args:
      v_ego: Current ego velocity (m/s)
      model_v2: modelV2 cereal message
      lat, lon: GPS coordinates (for learned speed lookup)

    Returns:
      (v_target, state, using_learned) where:
        - v_target: Target velocity (m/s) or None if no limit
        - state: Current VTSCState
        - using_learned: True if using learned speed from database
    """
    # Check if VTSC is enabled (refresh params at most once per second)
    enabled, curve_learn_enabled = self._get_cached_params()
    
    self.using_learned_speed = False
    self.learned_speed_kph = 0.0
    
    # Calculate lateral accelerations
    self._calculate_lateral_acceleration(v_ego, model_v2)

    # Estimate curvature for bucket lookup and calibration
    if self.max_pred_lat_acc > 0.1 and v_ego > 1.0:
      kappa = self.max_pred_lat_acc / (v_ego ** 2)
    else:
      kappa = 0.0

    # Update state machine
    self._update_state_machine(enabled, v_ego)

    # Calculate speed target if in entering or turning state
    if self.state in (VTSCState.entering, VTSCState.turning):
      # Try learned speed first (priority over vision calculation)
      if curve_learn_enabled and lat != 0.0 and lon != 0.0:
        learned_speed_ms = _common_get_learned_speed(lat, lon, kappa, curve_learn_enabled, min_speed_kph=15.0)

        if learned_speed_ms > 0:
          # Use learned driver speed
          self.v_target = learned_speed_ms
          self.using_learned_speed = True
          self.learned_speed_kph = learned_speed_ms * 3.6
        else:
          # Fall back to vision-based calculation (per-curvature-bucket a_comfort)
          self.v_target = self._calculate_speed_target(v_ego)
          self.using_learned_speed = False
      else:
        # Vision-based calculation (per-curvature-bucket a_comfort)
        self.v_target = self._calculate_speed_target(v_ego)
        self.using_learned_speed = False

      # Feed calibration with observed peak lat accel while actively turning
      # Bucketed by curvature so mild/medium/sharp curves learn independently
      self._calibrate(self.current_lat_acc, kappa)
    else:
      self.v_target = None
      self.using_learned_speed = False

    return self.v_target, self.state, self.using_learned_speed
