#!/usr/bin/env python3
"""
NagasPilot Turn Speed Controller (TSC) - Unified Curve Speed Management

EnhancedPilot-inspired unified controller combining:
- Map-based curve detection (GPS + OSM data) via MTSC
- Vision-based curve detection (modelV2) via VTSC  
- Self-calibrating lateral acceleration learning
- Intelligent fusion for safest speed limit

Uses EnhancedPilot's proven fusion approach while maintaining NagasPilot
architecture with np_ prefix conventions.

CAMERA INDEPENDENCE: ✅ Works on ALL products
- Vision detection: Uses modelV2 (always available)
- Map detection: Requires GPS + mapd binary (optional)
- Self-calibration: Learns from real driving (adaptive)

Integration: Called from longitudinal_planner.py to limit speed on curves
"""

import json
import math
import time
import numpy as np
from enum import Enum, auto
from typing import Optional, Dict, Tuple, Any

from cereal import log, custom
from openpilot.common.params import Params
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.common.conversions import Conversions as CV


class NpMapState(Enum):
    """Map turn controller states following NagasPilot patterns"""
    DISABLED = auto()
    ENABLED = auto()
    TURNING = auto()
    OVERRIDING = auto()


class NpTscController:
    """
    NagasPilot Turn Speed Controller - Unified map + vision + self-calibration
    
    Provides safe curve speed limits by intelligently fusing:
    1. Map data (MTSC - long lookahead, GPS-dependent)
    2. Vision data (VTSC - always available, shorter lookahead)  
    3. Self-learned lateral acceleration (personalized)
    
    Based on EnhancedPilot's proven architecture, adapted for NagasPilot.
    """
    
    # Physics constants (EnhancedPilot-compatible with NagasPilot tuning)
    TARGET_LAT_A_DEFAULT = 2.0      # m/s² default lateral acceleration (NagasPilot tuned)
    SAFETY_MARGIN = 2.0             # m/s safety reduction
    MIN_SPEED = 5.0                 # m/s minimum safe speed (20 km/h)
    CURVE_THRESHOLD = 0.002         # 1/m minimum curvature to engage
    MIN_VELOCITY = 0.3              # m/s minimum velocity for curvature calc
    
    # Map-based constants (EnhancedPilot proven values)
    MAX_LOOKAHEAD = 500.0           # m maximum distance to consider curves
    TARGET_JERK = -0.6              # m/s³ jerk limit during interventions
    TARGET_ACCEL = -1.2             # m/s² steady-state decel target
    TARGET_OFFSET = 1.0             # s offset (distance = speed * offset)
    MAP_DATA_STALE_S = 5.0          # seconds before map data considered stale
    
    # Self-calibration constants (EnhancedPilot pattern)
    CRUISING_SPEED = 8.0            # m/s minimum speed for training (29 km/h)
    PLANNER_TIME = 2.0              # seconds stable before sampling
    CALIBRATION_PROGRESS_THRESHOLD = 10  # samples per curvature bin
    MAX_CURVATURE = 0.1             # 1/m maximum trackable
    MIN_CURVATURE = 0.001           # 1/m minimum trackable
    PERCENTILE = 90                 # Conservative estimate
    ROUNDING_PRECISION = 5          # Decimal places for curvature keys
    STEP = 0.001                    # Curvature bin resolution
    MIN_LATERAL_ACCEL = 0.5         # m/s² minimum to record
    
    def __init__(self, CP, params: Params = None):
        """Initialize NagasPilot TSC with vehicle configuration"""
        self.CP = CP
        self.params = params or Params()
        
        # Feature toggles following NagasPilot patterns
        self.enabled = False
        self.use_map = True      # Enable map-based detection by default
        self.use_vision = True   # Enable vision-based detection by default
        self.calibrate = True    # Enable self-calibration by default
        self.use_map_speed_limit = False  # Optional map speed limit resolver
        
        # Core parameters
        self.target_lat_a = self.TARGET_LAT_A_DEFAULT
        
        # Map detection state (MTSC integration)
        self.map_state = NpMapState.DISABLED
        self.map_safe_speed = None
        self.last_gps_position = None
        self.v_target = 0.0
        self._last_mapd_raw = ""
        self._last_mapd_time = 0.0
        self._mapd_stale_logged = False
        self.map_fresh = False
        
        # Vision detection state (VTSC integration)
        self.vision_safe_speed = None
        self.current_curvature = 0.0
        self.curvature_filter = FirstOrderFilter(0.0, 0.3, DT_MDL)
        
        # Self-calibration state (Enhanced with FrogPilot proven patterns)
        self.enable_training = False
        self.training_timer = 0.0
        self.curvature_data = {}  # FrogPilot-style: {curvature: {"average": float, "count": int}}
        self.lateral_acceleration = self.TARGET_LAT_A_DEFAULT
        self.calibration_progress = 0.0
        self.required_curvatures = [
            str(round(c, self.ROUNDING_PRECISION))
            for c in np.arange(self.MIN_CURVATURE, self.MAX_CURVATURE + self.STEP, self.STEP)
        ]
        
        # FrogPilot-style training state tracking
        self.training_active = False
        self.last_training_update = 0.0
        
        # Load persistent calibration data
        self._load_calibration_data()
        
        # Parameter refresh (every 5 seconds, EnhancedPilot pattern)
        self.last_param_refresh = 0.0
        self.PARAM_REFRESH_S = 5.0
        
        # State tracking for NagasPilot integration
        self.state = custom.LongitudinalPlanExt.VisionTurnControllerState.disabled
        self.a_target = 0.0
        self.v_turn = 0.0
        
        cloudlog.info(f"NP TSC Controller initialized: {len(self.curvature_data)} curvature bins, "
                      f"{self.calibration_progress:.1f}% calibrated, "
                      f"lat_accel={self.lateral_acceleration:.2f} m/s²")

    def _get_float_param(self, key: str, default: Optional[float] = None) -> Optional[float]:
        """Safely read float parameters without blocking."""
        value = self.params.get(key)
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, (bytes, bytearray)):
            value = value.decode('utf-8', errors='ignore')
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _put_float_param(self, key: str, value: float) -> None:
        """Persist floats using the supported Params API."""
        self.params.put_nonblocking(key, f"{float(value)}".encode('utf-8'))

    def update(self, sm, enabled: bool, v_ego: float, a_ego: float, v_cruise: float) -> None:
        """
        Main update function for NagasPilot integration
        
        Args:
            sm: SubMaster with messaging data
            enabled: Longitudinal control enabled
            v_ego: Current vehicle speed (m/s)
            a_ego: Current vehicle acceleration (m/s²)
            v_cruise: Current cruise target (m/s)
        """
        # Refresh parameters periodically
        t_since_boot = time.monotonic()
        if t_since_boot - self.last_param_refresh > self.PARAM_REFRESH_S:
            self._refresh_params()
            self.last_param_refresh = t_since_boot
        
        # Check if enabled
        if not self.enabled:
            self.state = custom.LongitudinalPlanExt.VisionTurnControllerState.disabled
            self.a_target = a_ego
            self.v_turn = v_cruise
            return
        
        # Update self-calibration (if enabled)
        if self.calibrate:
            self._update_calibration(v_ego, sm, tracking_lead=False)
        
        # Use calibrated lateral acceleration
        self.target_lat_a = self.lateral_acceleration
        
        # Minimum speed to engage
        if v_ego < self.MIN_SPEED:
            self.state = custom.LongitudinalPlanExt.VisionTurnControllerState.disabled
            self.a_target = a_ego
            self.v_turn = v_cruise
            return
        
        # Get limits from each source using NagasPilot's existing controllers
        map_limit = None
        vision_limit = None
        # Mark map stale if nothing received recently
        now = time.monotonic()
        if self._last_mapd_time and (now - self._last_mapd_time) > self.MAP_DATA_STALE_S:
            self.map_fresh = False
        
        if self.use_map:
            map_limit = self._get_map_limit(v_ego, sm)
        
        if self.use_vision:
            vision_limit = self._get_vision_limit(v_ego, sm)
        
        # Intelligent fusion: use most conservative (safest) limit
        fused_limit = self._fuse_limits(map_limit, vision_limit)
        
        # Update state and targets
        if fused_limit is not None and fused_limit < v_cruise - 0.5:
            self.state = custom.LongitudinalPlanExt.VisionTurnControllerState.active
            self.v_turn = fused_limit
            # Calculate acceleration target to reach the limit
            self.a_target = self._calculate_accel_target(v_ego, fused_limit, a_ego)
        else:
            self.state = custom.LongitudinalPlanExt.VisionTurnControllerState.disabled
            self.v_turn = v_cruise
            self.a_target = a_ego
    
    def _get_map_limit(self, v_ego: float, sm) -> Optional[float]:
        """Get speed limit from map data using MTSC integration"""
        # Check if mapd data is available
        if not sm.valid.get('liveMapDataSP', False):
            self.map_fresh = False
            return None
        
        map_data = sm['liveMapDataSP']
        if not map_data or not getattr(map_data, "speedLimitValid", False):
            self.map_fresh = False
            return None
        
        # Freshness using receive time
        now = time.monotonic()
        stale = (now - self._last_mapd_time) > self.MAP_DATA_STALE_S if self._last_mapd_time else False
        self._last_mapd_time = now
        self.map_fresh = not stale

        current_speed_limit = map_data.speedLimit
        if current_speed_limit <= 0:
            return None
        
        curvature = getattr(map_data, "curvature", 0.0)
        if curvature and curvature > 0.0:
            safe_speed = math.sqrt(max(self.target_lat_a / curvature, 0.1)) - self.SAFETY_MARGIN
        else:
            safe_speed = current_speed_limit - self.SAFETY_MARGIN
        safe_speed = max(safe_speed, self.MIN_SPEED)
        
        return safe_speed if safe_speed < v_ego else None
    
    def _get_vision_limit(self, v_ego: float, sm) -> Optional[float]:
        """Get speed limit from vision data using VTSC integration"""
        # Check if model data is available
        if not sm.valid.get('modelV2', False):
            return None
        
        model_v2 = sm['modelV2']
        if not model_v2 or len(model_v2.position.x) == 0:
            return None
        
        # Use VTSC-style calculation with EnhancedPilot logic
        # Extract curvature from model data
        safe_speed = self._calculate_vision_safe_speed(v_ego, model_v2)
        
        return safe_speed if safe_speed < v_ego else None
    
    def _fuse_limits(self, map_limit: Optional[float], vision_limit: Optional[float]) -> Optional[float]:
        """
        Fuse map and vision limits intelligently (EnhancedPilot strategy)
        
        Strategy:
        - If both available: use minimum (most conservative)
        - If only one available: use that one
        - If neither available: no limit
        """
        if map_limit is not None and vision_limit is not None:
            # Both sources agree on a limit - use safer one
            return min(map_limit, vision_limit)
        elif map_limit is not None:
            # Only map available
            return map_limit
        elif vision_limit is not None:
            # Only vision available
            return vision_limit
        else:
            # No limits from any source
            return None
    
    def _calculate_map_safe_speed(self, v_ego: float, map_data) -> float:
        """Calculate safe speed from map data (EnhancedPilot logic)"""
        # Extract curvature from map data
        # This would use the actual map data fields from liveMapDataSP
        # For now, return a conservative estimate based on speed limit
        speed_limit_ms = map_data.speedLimit
        
        # Apply lateral acceleration limit
        # v = sqrt(a_lat / curvature)
        # For now, use conservative reduction
        safe_speed = speed_limit_ms - self.SAFETY_MARGIN
        
        return max(safe_speed, self.MIN_SPEED)
    
    def _calculate_vision_safe_speed(self, v_ego: float, model_v2) -> float:
        """Calculate safe speed from vision data (EnhancedPilot logic)"""
        # Extract curvature from model data
        if len(model_v2.position.x) < 3:
            return v_ego  # No curve data available
        
        # Calculate curvature from position data
        # This is a simplified version - real implementation would be more sophisticated
        x = np.array(model_v2.position.x)
        y = np.array(model_v2.position.y)
        
        # Fit polynomial and calculate curvature
        try:
            coeffs = np.polyfit(x, y, 3)
            # Calculate maximum curvature in the lookahead range
            max_curvature = self._calculate_max_curvature(coeffs, x)
            
            if max_curvature > self.CURVE_THRESHOLD:
                # Calculate safe speed: v = sqrt(a_lat / curvature)
                safe_speed = math.sqrt(self.target_lat_a / max_curvature) - self.SAFETY_MARGIN
                return max(safe_speed, self.MIN_SPEED)
        except:
            pass
        
        return v_ego  # No curve detected
    
    def _calculate_max_curvature(self, poly_coeffs, x_range) -> float:
        """Calculate maximum curvature from polynomial coefficients"""
        # Curvature formula: κ = |y''| / (1 + y'²)^(3/2)
        # For polynomial y = ax³ + bx² + cx + d
        # y' = 3ax² + 2bx + c
        # y'' = 6ax + 2b
        
        a, b, c, d = poly_coeffs
        max_curvature = 0.0
        
        for x in np.linspace(x_range[0], x_range[-1], 20):
            y_prime = 3*a*x**2 + 2*b*x + c
            y_double_prime = 6*a*x + 2*b
            
            if abs(y_double_prime) > 1e-6:  # Avoid division by zero
                curvature = abs(y_double_prime) / (1 + y_prime**2)**1.5
                max_curvature = max(max_curvature, curvature)
        
        return max_curvature
    
    def _calculate_accel_target(self, v_ego: float, target_speed: float, a_ego: float) -> float:
        """Calculate acceleration target to reach the speed limit (EnhancedPilot logic)"""
        # Calculate distance to target speed using EnhancedPilot's jerk-aware approach
        delta_v = target_speed - v_ego
        
        if delta_v >= 0:
            # Accelerating - use gentle acceleration
            return min(0.5, delta_v * 0.1)  # Conservative acceleration
        else:
            # Decelerating - use EnhancedPilot's jerk-limited approach
            # Time to reach target: t = sqrt(2 * |delta_v| / |target_accel|)
            # But limit by jerk: t = sqrt(|delta_v| / |target_jerk|)
            decel_time = math.sqrt(abs(delta_v) / abs(self.TARGET_JERK))
            
            # Calculate required acceleration
            required_accel = delta_v / decel_time
            
            # Limit to target acceleration
            return max(required_accel, self.TARGET_ACCEL)
    
    def _update_calibration_progress(self) -> None:
        """Calculate calibration progress 0-100% (Enhanced with FrogPilot proven patterns)"""
        if not self.required_curvatures:
            self.calibration_progress = 0.0
            return
        
        # FrogPilot-style: Progress based on count threshold per curvature bin
        progress = 0.0
        calibrated_bins = 0
        total_samples = 0
        
        for key in self.required_curvatures:
            if key in self.curvature_data and "count" in self.curvature_data[key]:
                count = self.curvature_data[key]["count"]
                # Each bin needs CALIBRATION_PROGRESS_THRESHOLD samples to be considered "calibrated"
                bin_progress = min(count / self.CALIBRATION_PROGRESS_THRESHOLD, 1.0)
                progress += bin_progress
                
                if count >= self.CALIBRATION_PROGRESS_THRESHOLD:
                    calibrated_bins += 1
                
                total_samples += count
        
        # Calculate overall progress (0-100%)
        self.calibration_progress = (progress / len(self.required_curvatures)) * 100.0
        
        # FrogPilot-style: Enhanced progress reporting
        if self.calibration_progress > 0:
            cloudlog.debug(f"NP TSC: Calibration progress {self.calibration_progress:.1f}% "
                         f"({calibrated_bins}/{len(self.required_curvatures)} bins calibrated, "
                         f"{total_samples} total samples)")

        # Save progress with FrogPilot-style non-blocking writes
        self._put_float_param("np_tsc_calibration_progress", self.calibration_progress)
        
        # Also track calibration state for user visibility
        if self.calibration_progress >= 100.0:
            self.params.put_bool_nonblocking("np_tsc_calibration_complete", True)
        else:
            self.params.put_bool_nonblocking("np_tsc_calibration_complete", False)
    
    def _update_calibration(self, v_ego: float, sm, tracking_lead: bool = False) -> None:
        """
        Update self-calibration with new driving data (Enhanced with FrogPilot proven patterns)
        
        Args:
            v_ego: Current speed (m/s)
            model_v2: Vision model data
            tracking_lead: True if following a lead vehicle
        """
        # FrogPilot-style training state management
        self.enable_training = v_ego > self.CRUISING_SPEED
        self.enable_training &= not tracking_lead
        
        # Additional FrogPilot-proven conditions
        if sm.valid.get('carControl', False):
            self.enable_training &= not sm['carControl'].longActive  # Not manually accelerating
        if sm.valid.get('carState', False):
            self.enable_training &= not (sm['carState'].leftBlinker or sm['carState'].rightBlinker)  # Not changing lanes
        
        # Update training state visibility
        if self.enable_training != self.training_active:
            self.training_active = self.enable_training
            self.params.put_bool_nonblocking("np_tsc_training_active", self.training_active)
        
        if self.enable_training:
            self.training_timer += DT_MDL
            
            # FrogPilot-style: Check if we're actually in a curve and stable
            if (self.training_timer >= self.PLANNER_TIME and 
                self._is_driving_in_curve(sm) and 
                self._is_stable_conditions(sm)):
                
                # Extract curvature from model data
                model_v2 = sm['modelV2'] if sm.valid.get('modelV2', False) else None
                if model_v2 and len(model_v2.position.x) >= 3:
                    curvature = self._extract_curvature_from_model(model_v2)
                    
                    if curvature > self.MIN_CURVATURE:
                        # Calculate lateral acceleration
                        lat_accel = v_ego**2 * curvature
                        
                        if lat_accel > self.MIN_LATERAL_ACCEL:
                            # Store in curvature bin using FrogPilot-style data structure
                            curvature_key = str(round(curvature, self.ROUNDING_PRECISION))

                            # FrogPilot-style: Store running average instead of raw samples
                            if curvature_key not in self.curvature_data:
                                self.curvature_data[curvature_key] = {
                                    "average": lat_accel,
                                    "count": 1
                                }
                            else:
                                # Update running average (FrogPilot proven pattern)
                                data = self.curvature_data[curvature_key]
                                count = data["count"]
                                current_avg = data["average"]

                                # Incremental average calculation
                                new_avg = ((current_avg * count) + lat_accel) / (count + 1)
                                self.curvature_data[curvature_key] = {
                                    "average": new_avg,
                                    "count": count + 1
                                }

                            # Update lateral acceleration for this curvature
                            self._update_lateral_acceleration(curvature_key)

                            # Update calibration progress
                            self._update_calibration_progress()

                            # Track last training update
                            self.last_training_update = time.monotonic()
            else:
                # Not in optimal training conditions
                self.enable_training = False
        else:
            # Reset training timer when conditions aren't met
            self.training_timer = 0.0
    
    def _is_driving_in_curve(self, sm) -> bool:
        """FrogPilot-style: Check if we're actually driving in a curve"""
        # Check if we have valid model data
        if not sm.valid.get('modelV2', False):
            return False
        
        model_v2 = sm['modelV2']
        if not model_v2 or len(model_v2.position.x) < 3:
            return False
        
        # Extract curvature from model (simplified version of our curvature extraction)
        try:
            x = np.array(model_v2.position.x[:10])
            y = np.array(model_v2.position.y[:10])
            
            # Calculate curvature using the same method as our calibration
            dx = np.gradient(x)
            dy = np.gradient(y)
            ddx = np.gradient(dx)
            ddy = np.gradient(dy)
            
            # Curvature formula: κ = |x'y'' - y'x''| / (x'² + y'²)^(3/2)
            curvature = np.abs(dx * ddy - dy * ddx) / (dx**2 + dy**2)**1.5
            
            # Check if we're in a meaningful curve (FrogPilot-style threshold)
            max_curvature = np.max(curvature) if len(curvature) > 0 else 0.0
            return max_curvature > self.CURVE_THRESHOLD
            
        except:
            return False
    
    def _is_stable_conditions(self, sm) -> bool:
        """FrogPilot-style: Check if conditions are stable for training"""
        # Basic stability checks
        if not sm.valid.get('carState', False):
            return False
        
        car_state = sm['carState']
        
        # Check for stable steering (not making large corrections)
        if abs(car_state.steeringAngleDeg) > 15.0:  # Large steering angle
            return False
        
        # Check for stable speed (not rapidly changing)
        if abs(car_state.aEgo) > 1.0:  # High acceleration/deceleration
            return False
        
        # Check for reasonable lateral acceleration (not sliding)
        if abs(car_state.lateralAcceleration) > 3.0:  # Too much side force
            return False
        
        return True
    
    def _extract_curvature_from_model(self, model_v2) -> float:
        """Extract curvature from modelV2 data"""
        if len(model_v2.position.x) < 3:
            return 0.0
        
        # Simple curvature calculation from position data
        x = np.array(model_v2.position.x[:10])  # Use first 10 points
        y = np.array(model_v2.position.y[:10])
        
        try:
            # Fit circle to points and calculate curvature
            # Simplified approach - real implementation would be more sophisticated
            dx = np.gradient(x)
            dy = np.gradient(y)
            ddx = np.gradient(dx)
            ddy = np.gradient(dy)
            
            # Curvature formula: κ = |x'y'' - y'x''| / (x'² + y'²)^(3/2)
            curvature = np.abs(dx * ddy - dy * ddx) / (dx**2 + dy**2)**1.5
            
            # Return maximum curvature
            return np.max(curvature) if len(curvature) > 0 else 0.0
        except:
            return 0.0
    
    def _update_lateral_acceleration(self, curvature_key: str) -> None:
        """Update lateral acceleration using FrogPilot-style average-based approach"""
        if curvature_key not in self.curvature_data:
            return
        
        # FrogPilot-style: Use running averages instead of raw samples
        # This is more memory efficient and provides stable estimates
        all_averages = []
        total_samples = 0
        
        for key, data in self.curvature_data.items():
            if "average" in data and data["count"] > 0:
                all_averages.append(data["average"])
                total_samples += data["count"]
        
        if len(all_averages) < 3:  # Need minimum curvature bins
            return
        
        # FrogPilot-style: Use percentile of averages for conservative estimate
        percentile_lat_accel = np.percentile(all_averages, self.PERCENTILE)
        
        # Update the overall lateral acceleration (conservative approach)
        self.lateral_acceleration = min(self.lateral_acceleration, percentile_lat_accel)
        
        # Save calibration data with FrogPilot-style progress tracking
        self._save_calibration_data()
        
        # Log training activity for debugging
        cloudlog.debug(f"NP TSC: Updated lateral acceleration to {self.lateral_acceleration:.2f} m/s² from {len(all_averages)} curvature bins")
    
    def _refresh_params(self) -> None:
        """Refresh parameters from persistent storage (Enhanced with FrogPilot patterns)"""
        self.enabled = self.params.get_bool("np_tsc_enable")
        self.use_map = self.params.get_bool("np_tsc_use_map")
        self.use_vision = self.params.get_bool("np_tsc_use_vision")
        self.calibrate = self.params.get_bool("np_tsc_calibrate")

        # FrogPilot-style: Load calibration state if available
        lat_accel = self._get_float_param("np_tsc_lateral_acceleration", None)
        if lat_accel is not None and lat_accel > 0:
            self.lateral_acceleration = lat_accel

        progress = self._get_float_param("np_tsc_calibration_progress", None)
        if progress is not None and progress >= 0:
            self.calibration_progress = progress

    def _load_calibration_data(self) -> None:
        """Load calibration data from persistent storage"""
        try:
            # Load curvature data (JSON)
            data_bytes = self.params.get("np_tsc_curvature_data")
            if data_bytes:
                self.curvature_data = json.loads(data_bytes.decode('utf-8'))

            # Load lateral acceleration
            lat_accel = self._get_float_param("np_tsc_lateral_acceleration", None)
            if lat_accel is not None:
                self.lateral_acceleration = lat_accel

            # Load calibration progress
            progress = self._get_float_param("np_tsc_calibration_progress", None)
            if progress is not None:
                self.calibration_progress = progress

        except Exception as e:
            cloudlog.error(f"NP TSC failed to load calibration data: {e}")
            self.curvature_data = {}
            self.lateral_acceleration = self.TARGET_LAT_A_DEFAULT
            self.calibration_progress = 0.0

    def _save_calibration_data(self) -> None:
        """Save calibration data to persistent storage (FrogPilot-style non-blocking approach)"""
        try:
            # FrogPilot-style: Save curvature data with non-blocking writes
            self.params.put_nonblocking("np_tsc_curvature_data", json.dumps(self.curvature_data))

            # FrogPilot-style: Save lateral acceleration as float for efficiency
            self._put_float_param("np_tsc_lateral_acceleration", self.lateral_acceleration)

            # FrogPilot-style: Enhanced calibration state tracking
            self._put_float_param("np_tsc_calibration_progress", self.calibration_progress)

            # Track last save time for debugging
            self._put_float_param("np_tsc_last_calibration_save", time.time())

        except Exception as e:
            cloudlog.error(f"NP TSC failed to save calibration data: {e}")
            # Don't crash the controller - just log the error
    
    def reset_calibration(self) -> None:
        """Reset all calibration data (NagasPilot style)"""
        self.curvature_data = {}
        self.lateral_acceleration = self.TARGET_LAT_A_DEFAULT
        self.calibration_progress = 0.0
        self._save_calibration_data()
        cloudlog.info("NP TSC: Calibration data reset")
    
    # Standardized NagasPilot interface
    @property
    def is_enabled(self) -> bool:
        """Check if TSC controller is enabled"""
        return self.enabled
    
    @property
    def is_active(self) -> bool:
        """Check if TSC is currently active"""
        return self.state != custom.LongitudinalPlanExt.VisionTurnControllerState.disabled
    
    @property
    def current_state(self):
        """Get current TSC state for NagasPilot integration"""
        return self.state
    
    def get_debug_info(self) -> Dict:
        """Get debug information following NagasPilot patterns (Enhanced with FrogPilot training info)"""
        return {
            "enabled": self.is_enabled,
            "active": self.is_active,
            "state": self.state.name if hasattr(self.state, 'name') else str(self.state),
            "use_map": self.use_map,
            "use_vision": self.use_vision,
            "calibrate": self.calibrate,
            "target_lat_a": self.target_lat_a,
            "lateral_acceleration": self.lateral_acceleration,
            "calibration_progress": self.calibration_progress,
            "training_active": self.training_active,
            "training_timer": self.training_timer,
            "curvature_bins": len(self.curvature_data),
            "v_turn": self.v_turn,
            "a_target": self.a_target,
            "last_training_update": self.last_training_update,
            "health_status": self._get_health_status()  # Add health monitoring
        }
    
    def _get_health_status(self) -> Dict[str, Any]:
        """Get comprehensive health status for monitoring and validation"""
        health_status = {
            "overall_health": "unknown",
            "controller_status": {
                "enabled": self.is_enabled,
                "active": self.is_active,
                "state": str(self.state)
            },
            "calibration_health": {
                "progress": self.calibration_progress,
                "training_active": self.training_active,
                "curvature_bins_trained": len([k for k, v in self.curvature_data.items() if v.get("count", 0) >= self.CALIBRATION_PROGRESS_THRESHOLD]),
                "total_curvature_bins": len(self.required_curvatures),
                "data_freshness": "fresh" if time.time() - self.last_training_update < 300 else "stale"
            },
            "data_integrity": {
                "curvature_data_valid": self._validate_curvature_data(),
                "lateral_acceleration_reasonable": 0.5 <= self.lateral_acceleration <= 4.0,  # Reasonable range
                "calibration_progress_valid": 0.0 <= self.calibration_progress <= 100.0
            },
            "performance_metrics": {
                "last_param_refresh": self.last_param_refresh,
                "training_success_rate": self._calculate_training_success_rate(),
                "memory_usage": len(self.curvature_data) * 2  # Approximate memory usage (keys + data)
            },
            "validation_status": {
                "parameters_loaded": self._validate_parameters(),
                "data_consistency": self._check_data_consistency(),
                "state_coherence": self._check_state_coherence()
            }
        }
        
        # Overall health assessment
        calibration_healthy = (health_status["calibration_health"]["progress"] >= 50.0 or 
                              health_status["calibration_health"]["training_active"] == False)
        data_integrity_healthy = all(health_status["data_integrity"].values())
        state_coherence_healthy = health_status["validation_status"]["state_coherence"]
        
        if (health_status["controller_status"]["enabled"] and 
            calibration_healthy and 
            data_integrity_healthy and 
            state_coherence_healthy):
            health_status["overall_health"] = "healthy"
        elif (health_status["controller_status"]["enabled"] and 
              data_integrity_healthy):
            health_status["overall_health"] = "degraded"
        else:
            health_status["overall_health"] = "unhealthy"
        
        return health_status
    
    def _validate_curvature_data(self) -> bool:
        """Validate curvature data integrity"""
        try:
            for curvature_key, data in self.curvature_data.items():
                # Check required keys
                if "average" not in data or "count" not in data:
                    return False
                
                # Check data types and ranges
                if not isinstance(data["average"], (int, float)):
                    return False
                if not isinstance(data["count"], int):
                    return False
                
                # Check reasonable ranges
                if data["average"] < 0 or data["average"] > 10:  # Reasonable lateral acceleration range
                    return False
                if data["count"] < 0:
                    return False
            
            return True
        except Exception as e:
            cloudlog.error(f"NP TSC: Curvature data validation failed: {e}")
            return False
    
    def _validate_parameters(self) -> bool:
        """Validate parameter loading and consistency"""
        try:
            # Check that our parameters are reasonable
            if not (0 <= self.calibration_progress <= 100):
                return False
            if not (0.1 <= self.lateral_acceleration <= 5.0):  # Reasonable lateral acceleration
                return False
            if not isinstance(self.enabled, bool):
                return False
            
            return True
        except Exception as e:
            cloudlog.error(f"NP TSC: Parameter validation failed: {e}")
            return False
    
    def _check_data_consistency(self) -> bool:
        """Check internal data consistency"""
        try:
            # Check that calibration progress matches curvature data
            expected_progress = self._calculate_expected_progress()
            if abs(self.calibration_progress - expected_progress) > 5.0:  # Allow 5% tolerance
                return False
            
            # Check that lateral acceleration is consistent with curvature data
            if self.curvature_data and self.lateral_acceleration < 0.5:  # Too low for having data
                return False
            
            return True
        except Exception as e:
            cloudlog.error(f"NP TSC: Data consistency check failed: {e}")
            return False
    
    def _check_state_coherence(self) -> bool:
        """Check that internal state is coherent"""
        try:
            # Check state transitions make sense
            if self.is_active and self.state == custom.LongitudinalPlanExt.VisionTurnControllerState.disabled:
                return False  # Active but disabled state is incoherent
            
            # Check that training state is consistent with data
            if self.training_active and len(self.curvature_data) == 0:
                return False  # Training active but no data is incoherent
            
            return True
        except Exception as e:
            cloudlog.error(f"NP TSC: State coherence check failed: {e}")
            return False
    
    def _calculate_training_success_rate(self) -> float:
        """Calculate training success rate based on data quality"""
        try:
            if not self.curvature_data:
                return 0.0
            
            total_attempts = sum(data.get("count", 0) for data in self.curvature_data.values())
            successful_bins = sum(1 for data in self.curvature_data.values() 
                                if data.get("count", 0) >= self.CALIBRATION_PROGRESS_THRESHOLD)
            
            if total_attempts == 0:
                return 0.0
            
            return (successful_bins / len(self.required_curvatures)) * 100.0
        except Exception as e:
            cloudlog.error(f"NP TSC: Training success rate calculation failed: {e}")
            return 0.0
    
    def _calculate_expected_progress(self) -> float:
        """Calculate expected calibration progress based on current data"""
        try:
            if not self.curvature_data:
                return 0.0
            
            progress = 0.0
            for key in self.required_curvatures:
                if key in self.curvature_data:
                    count = self.curvature_data[key].get("count", 0)
                    progress += min(count / self.CALIBRATION_PROGRESS_THRESHOLD, 1.0)
            
            return (progress / len(self.required_curvatures)) * 100.0
        except Exception as e:
            cloudlog.error(f"NP TSC: Expected progress calculation failed: {e}")
            return 0.0
