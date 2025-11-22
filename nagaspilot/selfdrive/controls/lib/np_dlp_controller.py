#!/usr/bin/env python3
"""
NagasPilot Dynamic Lane Profile Controller - Unified lane control system

A complete, production-ready replacement for DesireHelper that unifies:
- Laneful mode: Traditional lane line based control
- Laneless mode: Road edge/path based control  
- LCA mode: Lane change assistance with advanced features

Follows NagasPilot's clean architecture patterns with:
- Simple, predictable interface
- Comprehensive error handling
- Performance optimization
- Extensive monitoring and debugging
"""

import numpy as np
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Dict, Any, Tuple, Iterable
from collections import deque

from cereal import log, custom
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params
from openpilot.common.conversions import Conversions as CV
from openpilot.common.swaglog import cloudlog


class DLPMode(Enum):
    """Dynamic Lane Profile operational modes"""
    LANEFUL = auto()    # Traditional lane line based
    LANELESS = auto()   # Road edge/path based
    LCA = auto()        # Lane change assistance


class ALCCMode(Enum):
    """ALCC operational modes for user intention level control"""
    DISABLED = auto()       # Traditional operation (requires cruise)
    STEERING_ONLY = auto()  # Steering control only (MADS-like)
    FULL_CONTROL = auto()   # Full lateral control without cruise


class BrakeResponseMode(Enum):
    """Brake response modes for ALCC"""
    MAINTAIN = auto()    # Maintain lane control during braking
    PAUSE = auto()       # Temporarily pause lane control during braking
    DISENGAGE = auto()   # Completely disengage during braking


class LCUserIntention(Enum):
    """LCA user intention states for user intention level control"""
    NONE = auto()              # No lane change intention
    LEFT_DESIRE = auto()       # User wants to change left
    RIGHT_DESIRE = auto()      # User wants to change right
    LEFT_CONFIRMED = auto()    # User confirmed left lane change
    RIGHT_CONFIRMED = auto()   # User confirmed right lane change
    LEFT_EXECUTING = auto()    # Left lane change in progress
    RIGHT_EXECUTING = auto()   # Right lane change in progress
    LEFT_OVERRIDE = auto()     # User overriding left lane change
    RIGHT_OVERRIDE = auto()    # User overriding right lane change
    LEFT_CANCELLED = auto()    # Left lane change cancelled
    RIGHT_CANCELLED = auto()   # Right lane change cancelled


class LCAMode(Enum):
    """Lane Change Assistance modes"""
    OFF = auto()
    NUDGE = auto()
    TIMED = auto()
    ADAPTIVE = auto()


@dataclass
class DLPStatus:
    """Complete DLP controller status"""
    mode: DLPMode
    lca_mode: LCAMode
    desire: int
    confidence: float
    active: bool
    available: bool
    reason: str
    lateral_accel: float
    target_curvature: float
    lane_width_left: float
    lane_width_right: float
    blindspot_clear: bool
    lane_change_ready: bool


class NpDlpController:
    # Flow overview:
    # 1. __init__ loads Params and computes the per-mode tuning tables.
    # 2. update() consumes SubMaster data, updates mode/confidence, and emits
    #    a single `dlp_status` structure for downstream controllers.
    # 3. Helpers (_load_weekly_stats, _apply_mode_tuning, etc.) keep the
    #    planner logic modular so new data sources can be folded in easily.
    """
    NagasPilot Dynamic Lane Profile Controller
    
    Unified controller that manages all lane control functionality:
    - Automatic mode selection between laneful/laneless
    - Advanced LCA with multiple timing modes
    - Smooth transitions and comprehensive safety
    - Production-ready with extensive monitoring
    
    Drop-in replacement for DesireHelper with enhanced capabilities.
    """
    
    def __init__(self, CP, params: Params | None = None):
        self.CP = CP
        self.params = params or Params()
        
        # Feature toggles
        self.enabled = False
        self.lca_enabled = True
        self.laneless_enabled = True
        self.adaptive_timing = True
        self.weather_integration = True
        
        # Configuration parameters
        self._load_parameters()
        
        # State tracking
        self.current_mode = DLPMode.LANEFUL
        self.current_lca_mode = LCAMode.NUDGE
        self.desire = log.Desire.none
        self.confidence = 1.0
        
        # ALCC state tracking - user intention level control
        self.alcc_mode = ALCCMode.DISABLED
        self.brake_response_mode = BrakeResponseMode.MAINTAIN
        self.alcc_active = False
        self.alcc_standalone_active = False
        self.brake_response_active = False
        self.emergency_active = False
        
        # LCA state machine
        self.lane_change_state = log.LaneChangeState.off
        self.lane_change_direction = log.LaneChangeDirection.none
        self.lane_change_timer = 0.0
        self.lane_change_ll_prob = 1.0
        self.lane_change_wait_timer = 0.0
        self.prev_one_blinker = False
        self.lane_change_completed = False
        
        # Enhanced tracking
        self.lane_width_left = 0.0
        self.lane_width_right = 0.0
        self.blindspot_clear = True
        self.lane_change_ready = False
        self.road_curvature = 0.0
        
        # ALCC tracking
        self.brake_pressed_prev = False
        self.standalone_activations = 0
        self.emergency_activations = 0
        
        # LCA User Intention tracking (new)
        self.lca_user_intention = LCUserIntention.NONE
        self.lca_user_confirmed = False
        self.lca_user_override = False
        self.lca_intention_timer = 0.0
        self.lca_confirmation_timer = 0.0
        self.lca_intention_prev = LCUserIntention.NONE
        self.lca_activations_user = 0  # Track user-initiated LCA
        
        # Mode transition tracking
        self.mode_transition_progress = 1.0
        self.mode_transition_target = 1.0
        self.mode_history = deque(maxlen=100)
        
        # Filters for smooth operation
        self.confidence_filter = FirstOrderFilter(1.0, 1.5, DT_MDL)
        self.lateral_accel_filter = FirstOrderFilter(0.0, 2.0, DT_MDL)
        self.curvature_filter = FirstOrderFilter(0.0, 1.5, DT_MDL)
        self.lane_width_filter = FirstOrderFilter(3.5, 3.0, DT_MDL)
        
        # Performance metrics
        self.mode_durations = {mode: 0.0 for mode in DLPMode}
        self.mode_transitions = 0
        self.lca_activations = 0
        self.avg_confidence = 1.0
        self.last_mode_change = 0.0
        
        # Initialize from parameters
        self._initialize_from_params()
        
        cloudlog.info("NpDlpController initialized with unified lane control")
    
    def _get_bool_param(self, key: str, default: bool) -> bool:
        """Read boolean params without blocking when unset."""
        value = self.params.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (bytes, bytearray)):
            value = value.decode('utf-8', errors='ignore')
        if isinstance(value, str):
            return value.lower() not in ("0", "false", "off", "")
        return bool(value)

    def _get_float_param(self, key: str, default: float) -> float:
        """Safely parse float parameters from storage."""
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

    def _get_str_param(self, key: str, default: str) -> str:
        """Return string parameters with graceful fallback."""
        value = self.params.get(key)
        if value is None:
            return default
        if isinstance(value, (bytes, bytearray)):
            return value.decode('utf-8', errors='ignore')
        return str(value)

    def _get_str_param_with_fallback(self, keys: Iterable[str], default: str) -> Tuple[str, Optional[str]]:
        """Return the first available string value from a list of param keys."""
        for key in keys:
            if not key:
                continue
            raw = self.params.get(key)
            if raw is None:
                continue
            if isinstance(raw, (bytes, bytearray)):
                decoded = raw.decode('utf-8', errors='ignore')
            else:
                decoded = str(raw)
            if decoded:
                return decoded, key
        return default, None

    def _get_float_param_with_fallback(self, keys: Iterable[str], default: float) -> Tuple[float, Optional[str]]:
        """Return the first available float value from a list of param keys."""
        for key in keys:
            if not key:
                continue
            raw = self.params.get(key)
            if raw is None:
                continue
            if isinstance(raw, (int, float)):
                return float(raw), key
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode('utf-8', errors='ignore')
            try:
                return float(raw), key
            except (TypeError, ValueError):
                continue
        return default, None

    def _get_bool_param_with_fallback(self, keys: Iterable[str], default: bool) -> Tuple[bool, Optional[str]]:
        """Return the first available bool value from a list of param keys."""
        for key in keys:
            if not key:
                continue
            raw = self.params.get(key)
            if raw is None:
                continue
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode('utf-8', errors='ignore')
            if isinstance(raw, str):
                return raw.lower() not in ("0", "false", "off", ""), key
            return bool(raw), key
        return default, None

    def _load_parameters(self):
        """Load configuration parameters including ALCC and LCA user intention integration"""
        # Basic functionality
        self.enabled = self.params.get_bool("np_dlp_enable")
        self.lca_enabled = self._get_bool_param("np_dlp_lca_enable", True)
        self.laneless_enabled = self._get_bool_param("np_dlp_laneless_enable", True)

        # ALCC parameters - user intention level control
        self.alcc_mode_setting, alcc_mode_key = self._get_str_param_with_fallback(
            ("np_alcc_mode", "np_alcc_allow_standalone"), "DISABLED")
        if alcc_mode_key == "np_alcc_allow_standalone":
            self.params.put_nonblocking("np_alcc_mode", self.alcc_mode_setting)
        self.alcc_brake_response = self._get_str_param("np_alcc_brake_mode", "MAINTAIN")
        self.alcc_confidence_threshold = self._get_float_param("np_alcc_confidence_threshold", 0.7)
        self.alcc_hold_at_standstill = self._get_bool_param("np_alcc_hold_at_standstill", False)

        # LCA User Intention parameters - user intention level control
        self.lca_intention_enabled = self._get_bool_param("np_lca_intention_enable", True)
        self.lca_torque_threshold = self._get_float_param("np_lca_torque_threshold", 0.5)
        self.lca_auto_confirm_time = self._get_float_param("np_lca_auto_confirm_time", 0.5)
        self.lca_override_torque = self._get_float_param("np_lca_override_torque", 2.0)
        self.lca_intention_timeout = self._get_float_param("np_lca_intention_timeout", 10.0)
        self.lca_confidence_threshold = self._get_float_param("np_lca_confidence_threshold", 0.7)
        self.lca_max_curvature = self._get_float_param("np_lca_max_curvature", 0.01)

        # LCA timing modes (enhanced for user intention)
        self.lca_mode_setting, lca_mode_key = self._get_str_param_with_fallback(
            ("np_lca_mode", "np_dlp_lca_mode"), "NUDGE")
        if lca_mode_key == "np_dlp_lca_mode":
            self.params.put_nonblocking("np_lca_mode", self.lca_mode_setting)

        self.lca_delay, lca_delay_key = self._get_float_param_with_fallback(
            ("np_lca_auto_delay", "np_dlp_lca_delay"), 0.5)
        if lca_delay_key == "np_dlp_lca_delay":
            self.params.put_nonblocking("np_lca_auto_delay", f"{self.lca_delay:.2f}")

        self.lca_bsm_delay, lca_bsm_key = self._get_bool_param_with_fallback(
            ("np_lca_bsm_delay", "np_dlp_lca_bsm_delay"), True)
        if lca_bsm_key == "np_dlp_lca_bsm_delay":
            self.params.put_bool_nonblocking("np_lca_bsm_delay", self.lca_bsm_delay)

        # Laneless parameters
        self.laneless_min_confidence = self._get_float_param("np_dlp_laneless_min_confidence", 0.6)
        self.laneless_lateral_factor = self._get_float_param("np_dlp_laneless_lateral_factor", 0.7)
        self.laneless_transition_time = self._get_float_param("np_dlp_laneless_transition_time", 2.0)

        # Advanced features
        self.adaptive_timing = self._get_bool_param("np_dlp_adaptive_timing", True)
        self.weather_integration = self._get_bool_param("np_dlp_weather_integration", True)

        # Safety parameters
        self.min_lane_width = self._get_float_param("np_dlp_min_lane_width", 2.5)
        self.max_lateral_accel = self._get_float_param("np_dlp_max_lateral_accel", 2.5)
        self.confidence_threshold = self._get_float_param("np_dlp_confidence_threshold", 0.7)

        # Speed thresholds
        lca_min_speed_mph, lca_min_speed_key = self._get_float_param_with_fallback(
            ("np_lca_min_speed", "np_dlp_lca_min_speed"), 20.0)
        if lca_min_speed_key == "np_dlp_lca_min_speed":
            self.params.put_nonblocking("np_lca_min_speed", f"{int(round(lca_min_speed_mph))}")
        self.lca_min_speed = lca_min_speed_mph * CV.MPH_TO_MS
        self.laneless_max_speed = self._get_float_param("np_dlp_laneless_max_speed", 80.0) * CV.MPH_TO_MS
    
    def _initialize_from_params(self):
        """Initialize controller state from parameters including ALCC and LCA user intention"""
        # Set LCA mode from parameter
        try:
            self.current_lca_mode = LCAMode[self.lca_mode_setting.upper()]
        except KeyError:
            self.current_lca_mode = LCAMode.NUDGE
            cloudlog.warning(f"Invalid LCA mode {self.lca_mode_setting}, defaulting to NUDGE")
        
        # Set LCA user intention mode from parameter
        if self.lca_intention_enabled:
            # LCA user intention is enabled by default, start in NONE state
            self.lca_user_intention = LCUserIntention.NONE
            self.lca_user_confirmed = False
            self.lca_user_override = False
            cloudlog.info("LCA user intention level control enabled")
        
        # Set ALCC mode from parameter
        try:
            self.alcc_mode = ALCCMode[self.alcc_mode_setting.upper()]
        except KeyError:
            self.alcc_mode = ALCCMode.DISABLED
            cloudlog.warning(f"Invalid ALCC mode {self.alcc_mode_setting}, defaulting to DISABLED")

        try:
            self.brake_response_mode = BrakeResponseMode[self.alcc_brake_response.upper()]
        except KeyError:
            self.brake_response_mode = BrakeResponseMode.MAINTAIN
            cloudlog.warning(f"Invalid brake response mode {self.alcc_brake_response}, defaulting to MAINTAIN")

        self.last_param_refresh = time.monotonic()
        self.param_refresh_interval = 2.0  # Refresh every 2 seconds
        
        if self.lca_intention_enabled:
            cloudlog.info(f"DLP LCA user intention initialized: torque_threshold={self.lca_torque_threshold}, auto_confirm={self.lca_auto_confirm_time}")
        
    def update(self, sm, lateral_active: bool, v_ego: float, lat_allowed: bool, standstill: bool) -> DLPStatus:
        """
        Main update function - unified lane control with ALCC integration
        
        Args:
            sm: SubMaster with sensor data
            lateral_active: Whether lateral control is active
            v_ego: Vehicle speed (m/s)
            lat_allowed: Whether lateral control is allowed
            standstill: Whether vehicle is at standstill
            
        Returns:
            DLPStatus with complete controller state including ALCC
        """
        if not self.enabled:
            return self._get_disabled_status()
        
        start_time = time.time()
        
        # Refresh parameters periodically
        self._refresh_params_if_needed()
        
        # Assess current conditions
        conditions = self._assess_conditions(sm, v_ego, lat_allowed, standstill)
        
        # Apply ALCC logic first - user intention level control
        alcc_conditions = self._apply_alcc_logic(sm, conditions)
        
        # Determine optimal mode based on conditions (including ALCC)
        optimal_mode = self._determine_optimal_mode(alcc_conditions)
        
        # Handle mode transitions
        self._handle_mode_transition(optimal_mode, alcc_conditions)
        
        # Execute mode-specific logic with ALCC considerations
        if self.current_mode == DLPMode.LANEFUL:
            desire, control_params = self._execute_laneful_mode(alcc_conditions)
        elif self.current_mode == DLPMode.LANELESS:
            desire, control_params = self._execute_laneless_mode(alcc_conditions)
        else:  # DLPMode.LCA
            desire, control_params = self._execute_lca_mode(alcc_conditions)
        
        # Apply final ALCC modifications to control output
        final_desire, final_params = self._apply_alcc_output_modifications(desire, control_params, alcc_conditions)
        
        # Update performance metrics
        self._update_metrics(alcc_conditions, start_time)
        
        # Build final status with both ALCC and LCA information
        return self._build_status_with_lca(alcc_conditions, final_desire, final_params)
    
    def _assess_conditions(self, sm, v_ego: float, lat_allowed: bool, standstill: bool) -> Dict[str, Any]:
        """Comprehensive assessment of current driving conditions"""
        conditions = {
            "v_ego": v_ego,
            "lat_allowed": lat_allowed,
            "standstill": standstill,
            "timestamp": time.monotonic()
        }
        
        # Basic vehicle state
        if sm.valid.get('carState', False):
            carstate = sm['carState']
            conditions.update({
                "left_blinker": carstate.leftBlinker,
                "right_blinker": carstate.rightBlinker,
                "steering_pressed": carstate.steeringPressed,
                "steering_torque": getattr(carstate, 'steeringTorque', 0.0),
                "left_blindspot": getattr(carstate, 'leftBlindspot', False),
                "right_blindspot": getattr(carstate, 'rightBlindspot', False),
                "brake_pressed": carstate.brakePressed,
            })
        
        # Model data assessment
        if sm.valid.get('modelV2', False):
            model_v2 = sm['modelV2']
            conditions.update(self._assess_model_conditions(model_v2, v_ego))
        else:
            conditions.update({
                "laneful_confidence": 0.0,
                "laneless_confidence": 0.5,
                "lane_width_left": 0.0,
                "lane_width_right": 0.0,
                "road_edge_available": False,
                "lane_line_quality": 0.0,
                "reason": "No model data available"
            })
        
        # Weather and environmental conditions
        if self.weather_integration and sm.valid.get('weather', False):
            conditions.update(self._assess_weather_conditions(sm['weather']))
        
        # Road curvature assessment
        if sm.valid.get('modelV2', False):
            conditions.update(self._assess_road_curvature(sm['modelV2'], v_ego))
        
        return conditions
    
    def _apply_alcc_logic(self, sm, base_conditions: Dict[str, Any]) -> Dict[str, Any]:
        """Apply ALCC logic at user intention level - always controls steering wheel"""
        conditions = base_conditions.copy()
        
        # If ALCC is not enabled, return base conditions unchanged
        # Get vehicle state for ALCC logic
        if not sm.valid.get('carState', False):
            conditions.update({
                "alcc_active": False,
                "alcc_standalone_active": False,
                "alcc_reason": "No car state",
                "alcc_modified_lat_allowed": base_conditions["lat_allowed"]
            })
            return conditions
        
        carstate = sm['carState']
        
        # ALCC core logic - determines if ALCC should control steering
        alcc_eligible = self._check_alcc_eligibility(carstate, base_conditions)
        standalone_eligible = self._check_standalone_eligibility(carstate, base_conditions)
        
        # Update ALCC state
        self.alcc_active = alcc_eligible
        self.alcc_standalone_active = standalone_eligible and (self.alcc_mode != ALCCMode.DISABLED)
        
        # Modify lateral control allowance for ALCC
        # ALCC should always control steering when active, regardless of cruise state
        modified_lat_allowed = base_conditions["lat_allowed"] or self.alcc_active
        
        # Apply brake response logic
        brake_modified_conditions = self._apply_brake_response_logic(carstate, conditions, modified_lat_allowed)
        
        # Apply LCA user intention logic - USER INTENTION LEVEL (new)
        if self.lca_intention_enabled:
            lca_conditions = self._apply_lca_intention_logic(carstate, brake_modified_conditions, sm)
        else:
            # Legacy LCA behavior - use existing LCA state machine
            lca_conditions = self._apply_legacy_lca_logic(carstate, brake_modified_conditions, sm)
        
        # Update conditions with combined ALCC and LCA information
        final_conditions = lca_conditions.copy()
        final_conditions.update({
            "alcc_active": self.alcc_active,
            "alcc_standalone_active": self.alcc_standalone_active,
            "alcc_mode": self.alcc_mode,
            "alcc_reason": self._get_alcc_reason(carstate, base_conditions),
            "alcc_modified_lat_allowed": modified_lat_allowed,
            "cruise_enabled": carstate.cruiseState.enabled,
            "brake_pressed": carstate.brakePressed,
            "alcc_confidence": min(self.confidence, self.alcc_confidence_threshold)
        })
        
        return final_conditions
    
    def _apply_alcc_output_modifications(self, base_desire: int, base_params: Dict[str, float], alcc_conditions: Dict[str, Any]) -> Tuple[int, Dict[str, float]]:
        """Apply final ALCC modifications to control output"""
        
        # Start with base values
        final_desire = base_desire
        final_params = base_params.copy()
        
        # If ALCC is not active, return base values unchanged
        if not alcc_conditions.get("alcc_active", False):
            return final_desire, final_params
        
        # Apply standalone mode modifications
        if alcc_conditions.get("alcc_standalone_active", False):
            # For standalone operation, ensure we have steering control
            if final_desire == log.Desire.none and self.alcc_mode != ALCCMode.DISABLED:
                # Provide basic lane keeping when no other desire is active
                final_desire = log.Desire.laneKeep
            
            # Reduce aggressiveness for standalone operation
            safety_factor = 0.8 if self.alcc_mode == ALCCMode.STEERING_ONLY else 1.0
            final_params["lateral_accel"] = final_params.get("lateral_accel", 0.0) * safety_factor
            final_params["curvature"] = final_params.get("curvature", 0.0) * safety_factor
        
        # Apply brake response modifications
        if alcc_conditions.get("brake_response_active", False):
            if self.brake_response_mode == BrakeResponseMode.PAUSE:
                # Reduce control during braking
                final_params["lateral_accel"] = final_params.get("lateral_accel", 0.0) * 0.5
                final_params["curvature"] = final_params.get("curvature", 0.0) * 0.5
                final_params["confidence"] = final_params.get("confidence", 1.0) * 0.7
            elif self.brake_response_mode == BrakeResponseMode.DISENGAGE:
                # Complete disengagement
                final_desire = log.Desire.none
                final_params["lateral_accel"] = 0.0
                final_params["curvature"] = 0.0
                final_params["confidence"] = 0.0
        
        # Apply confidence threshold for ALCC
        alcc_confidence = alcc_conditions.get("alcc_confidence", 1.0)
        if final_params.get("confidence", 1.0) > alcc_confidence:
            final_params["confidence"] = alcc_confidence
        
        return final_desire, final_params
    
    def _apply_lca_intention_logic(self, carstate, base_conditions: Dict[str, Any], sm) -> Dict[str, Any]:
        """Apply LCA user intention logic - USER INTENTION LEVEL CONTROL"""
        conditions = base_conditions.copy()
        
        # Store previous intention for transition detection
        self.lca_intention_prev = self.lca_user_intention
        
        # Assess current user intention
        new_intention = self._assess_lca_user_intention(carstate, conditions)
        
        # Check for user override during execution
        override_detected, override_intention = self._check_lca_user_override(carstate, self.lca_user_intention)
        if override_detected:
            new_intention = override_intention
        
        # Validate intention against safety constraints
        if sm.valid.get('modelV2', False):
            intention_valid, safety_issues = self._validate_lca_user_intention(new_intention, conditions, sm['modelV2'])
            if not intention_valid:
                # Safety issues detected - cancel intention
                if new_intention in [LCUserIntention.LEFT_CONFIRMED, LCUserIntention.LEFT_EXECUTING]:
                    new_intention = LCUserIntention.LEFT_CANCELLED
                elif new_intention in [LCUserIntention.RIGHT_CONFIRMED, LCUserIntention.RIGHT_EXECUTING]:
                    new_intention = LCUserIntention.RIGHT_CANCELLED
        
        # Handle intention state transitions
        final_intention = self._handle_lca_intention_transitions(self.lca_user_intention, new_intention, conditions)
        
        # Update LCA user intention state
        self.lca_user_intention = final_intention
        self.lca_user_confirmed = final_intention in [LCUserIntention.LEFT_CONFIRMED, LCUserIntention.RIGHT_CONFIRMED]
        self.lca_user_override = final_intention in [LCUserIntention.LEFT_OVERRIDE, LCUserIntention.RIGHT_OVERRIDE]
        
        # Integrate LCA intention with conditions
        enhanced_conditions = self._integrate_lca_intention(final_intention, conditions, sm)
        
        return enhanced_conditions
    
    def _assess_lca_user_intention(self, carstate, conditions: Dict[str, Any]) -> LCUserIntention:
        """Assess user lane change intention from all inputs - USER INTENTION LEVEL"""
        current_time = time.monotonic()
        
        # Primary intention: Turn signal state
        left_signal = carstate.leftBlinker
        right_signal = carstate.rightBlinker
        
        # Secondary confirmation: Steering torque
        steering_torque = getattr(carstate, 'steeringTorque', 0.0)
        left_torque = steering_torque < -self.lca_torque_threshold
        right_torque = steering_torque > self.lca_torque_threshold
        
        # Tertiary signals: Speed and brake patterns
        brake_pressed = carstate.brakePressed
        gas_pressed = getattr(carstate, 'gasPressed', False)
        
        # Determine user intention based on current state
        if left_signal and not right_signal:
            return self._handle_left_lca_intention(left_torque, brake_pressed, gas_pressed, current_time)
        elif right_signal and not left_signal:
            return self._handle_right_lca_intention(right_torque, brake_pressed, gas_pressed, current_time)
        elif not left_signal and not right_signal:
            return self._handle_no_lca_intention(current_time)
        else:
            # Both signals - cancel any intention
            return LCUserIntention.NONE
    
    def _handle_left_lca_intention(self, torque_confirmed, brake_pressed, gas_pressed, current_time):
        """Handle left lane change user intention"""
        if self.lca_user_intention == LCUserIntention.LEFT_DESIRE:
            # Already have left desire - check for confirmation
            if torque_confirmed:
                self.lca_confirmation_timer = current_time
                return LCUserIntention.LEFT_CONFIRMED
            elif brake_pressed:
                # Brake cancels intention
                return LCUserIntention.LEFT_CANCELLED
            elif current_time - self.lca_intention_timer > self.lca_auto_confirm_time:
                # Auto-confirm after timeout (TIMED/ADAPTIVE mode behavior)
                if self.current_lca_mode in [LCAMode.TIMED, LCAMode.ADAPTIVE]:
                    self.lca_confirmation_timer = current_time
                    return LCUserIntention.LEFT_CONFIRMED
            return LCUserIntention.LEFT_DESIRE
            
        elif self.lca_user_intention in [LCUserIntention.NONE, LCUserIntention.LEFT_CANCELLED]:
            # New left intention
            self.lca_intention_timer = current_time
            return LCUserIntention.LEFT_DESIRE
            
        elif self.lca_user_intention == LCUserIntention.RIGHT_DESIRE:
            # Switching from right to left - cancel right first
            return LCUserIntention.RIGHT_CANCELLED
            
        else:
            # Already in left confirmed/executing - maintain state
            return self.lca_user_intention
    
    def _handle_right_lca_intention(self, torque_confirmed, brake_pressed, gas_pressed, current_time):
        """Handle right lane change user intention"""
        if self.lca_user_intention == LCUserIntention.RIGHT_DESIRE:
            # Already have right desire - check for confirmation
            if torque_confirmed:
                self.lca_confirmation_timer = current_time
                return LCUserIntention.RIGHT_CONFIRMED
            elif brake_pressed:
                # Brake cancels intention
                return LCUserIntention.RIGHT_CANCELLED
            elif current_time - self.lca_intention_timer > self.lca_auto_confirm_time:
                # Auto-confirm after timeout (TIMED/ADAPTIVE mode behavior)
                if self.current_lca_mode in [LCAMode.TIMED, LCAMode.ADAPTIVE]:
                    self.lca_confirmation_timer = current_time
                    return LCUserIntention.RIGHT_CONFIRMED
            return LCUserIntention.RIGHT_DESIRE
            
        elif self.lca_user_intention in [LCUserIntention.NONE, LCUserIntention.RIGHT_CANCELLED]:
            # New right intention
            self.lca_intention_timer = current_time
            return LCUserIntention.RIGHT_DESIRE
            
        elif self.lca_user_intention == LCUserIntention.LEFT_DESIRE:
            # Switching from left to right - cancel left first
            return LCUserIntention.LEFT_CANCELLED
            
        else:
            # Already in right confirmed/executing - maintain state
            return self.lca_user_intention
    
    def _handle_no_lca_intention(self, current_time):
        """Handle no lane change user intention"""
        # Check if intention has timed out
        if (self.lca_user_intention not in [LCUserIntention.NONE, LCUserIntention.LEFT_CANCELLED, LCUserIntention.RIGHT_CANCELLED] and
            current_time - self.lca_intention_timer > self.lca_intention_timeout):
            # Intention timed out - cancel appropriately
            if self.lca_user_intention in [LCUserIntention.LEFT_DESIRE, LCUserIntention.LEFT_CONFIRMED, LCUserIntention.LEFT_EXECUTING]:
                return LCUserIntention.LEFT_CANCELLED
            elif self.lca_user_intention in [LCUserIntention.RIGHT_DESIRE, LCUserIntention.RIGHT_CONFIRMED, LCUserIntention.RIGHT_EXECUTING]:
                return LCUserIntention.RIGHT_CANCELLED
        
        # No signal - maintain current state or stay in NONE
        return self.lca_user_intention if self.lca_user_intention in [LCUserIntention.LEFT_CANCELLED, LCUserIntention.RIGHT_CANCELLED] else LCUserIntention.NONE
    
    def _validate_lca_user_intention(self, intention, conditions: Dict[str, Any], model_v2) -> Tuple[bool, List[str]]:
        """Validate user intention against safety and environmental constraints"""
        safety_issues = []
        
        if intention in [LCUserIntention.LEFT_CONFIRMED, LCUserIntention.LEFT_EXECUTING]:
            # Left lane change validation
            if conditions.get("left_blindspot", False):
                safety_issues.append("Left blindspot detected")
            if conditions.get("left_lane_width", 3.5) < self.min_lane_width:
                safety_issues.append("Left lane too narrow")
            if conditions.get("left_lane_confidence", 1.0) < self.lca_confidence_threshold:
                safety_issues.append("Low left lane confidence")
                
        elif intention in [LCUserIntention.RIGHT_CONFIRMED, LCUserIntention.RIGHT_EXECUTING]:
            # Right lane change validation
            if conditions.get("right_blindspot", False):
                safety_issues.append("Right blindspot detected")
            if conditions.get("right_lane_width", 3.5) < self.min_lane_width:
                safety_issues.append("Right lane too narrow")
            if conditions.get("right_lane_confidence", 1.0) < self.lca_confidence_threshold:
                safety_issues.append("Low right lane confidence")
        
        # Environmental validation
        if conditions.get("road_curvature", 0.0) > self.lca_max_curvature:
            safety_issues.append("Road too curved for lane change")
        
        if conditions.get("v_ego", 0.0) < self.lca_min_speed:
            safety_issues.append("Speed too low for lane change")
        
        # Model validation
        if model_v2 and len(model_v2.laneLines) >= 4:
            lane_confidence = self._calculate_lane_confidence(model_v2)
            if lane_confidence < self.lca_confidence_threshold:
                safety_issues.append("Low lane detection confidence")
        
        return len(safety_issues) == 0, safety_issues
    
    def _check_lca_user_override(self, carstate, current_intention) -> Tuple[bool, LCUserIntention]:
        """Check if user is overriding current LCA execution"""
        if current_intention not in [LCUserIntention.LEFT_EXECUTING, LCUserIntention.RIGHT_EXECUTING]:
            return False, LCUserIntention.NONE
        
        steering_torque = getattr(carstate, 'steeringTorque', 0.0)
        steering_pressed = carstate.steeringPressed
        
        # Strong steering input indicates override
        if abs(steering_torque) > self.lca_override_torque:
            if current_intention == LCUserIntention.LEFT_EXECUTING and steering_torque > 0:
                return True, LCUserIntention.LEFT_OVERRIDE
            elif current_intention == LCUserIntention.RIGHT_EXECUTING and steering_torque < 0:
                return True, LCUserIntention.RIGHT_OVERRIDE
        
        # Steering wheel touch also indicates override
        if steering_pressed:
            return True, LCUserIntention.LEFT_OVERRIDE if current_intention == LCUserIntention.LEFT_EXECUTING else LCUserIntention.RIGHT_OVERRIDE
        
        return False, current_intention
    
    def _handle_lca_intention_transitions(self, current_intention, new_intention, conditions):
        """Handle state transitions for LCA user intention"""
        # Valid transitions
        valid_transitions = {
            LCUserIntention.NONE: [LCUserIntention.LEFT_DESIRE, LCUserIntention.RIGHT_DESIRE],
            LCUserIntention.LEFT_DESIRE: [LCUserIntention.LEFT_CONFIRMED, LCUserIntention.LEFT_CANCELLED, LCUserIntention.RIGHT_DESIRE],
            LCUserIntention.LEFT_CONFIRMED: [LCUserIntention.LEFT_EXECUTING, LCUserIntention.LEFT_CANCELLED],
            LCUserIntention.LEFT_EXECUTING: [LCUserIntention.NONE, LCUserIntention.LEFT_CANCELLED, LCUserIntention.LEFT_OVERRIDE],
            LCUserIntention.LEFT_OVERRIDE: [LCUserIntention.LEFT_CANCELLED, LCUserIntention.NONE],
            LCUserIntention.LEFT_CANCELLED: [LCUserIntention.NONE],
            LCUserIntention.RIGHT_DESIRE: [LCUserIntention.RIGHT_CONFIRMED, LCUserIntention.RIGHT_CANCELLED, LCUserIntention.LEFT_DESIRE],
            LCUserIntention.RIGHT_CONFIRMED: [LCUserIntention.RIGHT_EXECUTING, LCUserIntention.RIGHT_CANCELLED],
            LCUserIntention.RIGHT_EXECUTING: [LCUserIntention.NONE, LCUserIntention.RIGHT_CANCELLED, LCUserIntention.RIGHT_OVERRIDE],
            LCUserIntention.RIGHT_OVERRIDE: [LCUserIntention.RIGHT_CANCELLED, LCUserIntention.NONE],
            LCUserIntention.RIGHT_CANCELLED: [LCUserIntention.NONE],
        }
        
        # Check if transition is valid
        if new_intention in valid_transitions.get(current_intention, []):
            return new_intention
        else:
            # Invalid transition - maintain current state
            return current_intention
    
    def _integrate_lca_intention(self, intention, conditions: Dict[str, Any], sm) -> Dict[str, Any]:
        """Integrate LCA user intention with conditions"""
        enhanced_conditions = conditions.copy()
        
        # Add LCA intention information
        enhanced_conditions.update({
            "lca_user_intention": intention,
            "lca_user_confirmed": intention in [LCUserIntention.LEFT_CONFIRMED, LCUserIntention.RIGHT_CONFIRMED],
            "lca_user_override": intention in [LCUserIntention.LEFT_OVERRIDE, LCUserIntention.RIGHT_OVERRIDE],
            "lca_direction": self._get_lca_direction(intention),
            "lca_confidence": self._calculate_lca_confidence(intention, sm.get('modelV2') if sm.valid.get('modelV2', False) else None),
            "lca_reason": self._get_lca_reason(intention),
            "lca_active": intention in [LCUserIntention.LEFT_CONFIRMED, LCUserIntention.RIGHT_CONFIRMED, LCUserIntention.LEFT_EXECUTING, LCUserIntention.RIGHT_EXECUTING]
        })
        
        # Modify lateral control allowance for LCA
        # LCA should always allow steering when user intends lane change
        if intention in [LCUserIntention.LEFT_CONFIRMED, LCUserIntention.RIGHT_CONFIRMED, LCUserIntention.LEFT_EXECUTING, LCUserIntention.RIGHT_EXECUTING]:
            enhanced_conditions["lat_allowed"] = True  # Always allow when user intends lane change
        
        return enhanced_conditions
    
    def _get_lca_direction(self, intention) -> str:
        """Get LCA direction from intention"""
        if intention in [LCUserIntention.LEFT_DESIRE, LCUserIntention.LEFT_CONFIRMED, LCUserIntention.LEFT_EXECUTING, LCUserIntention.LEFT_OVERRIDE, LCUserIntention.LEFT_CANCELLED]:
            return "left"
        elif intention in [LCUserIntention.RIGHT_DESIRE, LCUserIntention.RIGHT_CONFIRMED, LCUserIntention.RIGHT_EXECUTING, LCUserIntention.RIGHT_OVERRIDE, LCUserIntention.RIGHT_CANCELLED]:
            return "right"
        else:
            return "none"
    
    def _calculate_lca_confidence(self, intention, model_v2) -> float:
        """Calculate LCA confidence based on intention and conditions"""
        if intention in [LCUserIntention.LEFT_CONFIRMED, LCUserIntention.RIGHT_CONFIRMED, LCUserIntention.LEFT_EXECUTING, LCUserIntention.RIGHT_EXECUTING]:
            # High confidence for confirmed/executing intentions
            base_confidence = 0.9
        elif intention in [LCUserIntention.LEFT_DESIRE, LCUserIntention.RIGHT_DESIRE]:
            # Medium confidence for desire state
            base_confidence = 0.7
        else:
            # Low confidence for cancelled/no intention
            base_confidence = 0.3
        
        # Apply confidence threshold
        return min(base_confidence, self.lca_confidence_threshold)
    
    def _get_lca_reason(self, intention) -> str:
        """Get human-readable reason for LCA state"""
        reason_map = {
            LCUserIntention.NONE: "No lane change intention",
            LCUserIntention.LEFT_DESIRE: "Left lane change desired",
            LCUserIntention.RIGHT_DESIRE: "Right lane change desired",
            LCUserIntention.LEFT_CONFIRMED: "Left lane change confirmed",
            LCUserIntention.RIGHT_CONFIRMED: "Right lane change confirmed",
            LCUserIntention.LEFT_EXECUTING: "Left lane change executing",
            LCUserIntention.RIGHT_EXECUTING: "Right lane change executing",
            LCUserIntention.LEFT_OVERRIDE: "Left lane change override",
            LCUserIntention.RIGHT_OVERRIDE: "Right lane change override",
            LCUserIntention.LEFT_CANCELLED: "Left lane change cancelled",
            LCUserIntention.RIGHT_CANCELLED: "Right lane change cancelled",
        }
        return reason_map.get(intention, "Unknown LCA state")
    
    def _apply_legacy_lca_logic(self, carstate, base_conditions: Dict[str, Any], sm) -> Dict[str, Any]:
        """Apply legacy LCA logic for backward compatibility"""
        # Use existing LCA state machine logic
        conditions = base_conditions.copy()
        
        # Add basic LCA information for legacy mode
        conditions.update({
            "lca_user_intention": LCUserIntention.NONE,
            "lca_user_confirmed": False,
            "lca_user_override": False,
            "lca_direction": "none",
            "lca_confidence": 1.0,
            "lca_reason": "Legacy LCA mode",
            "lca_active": self.lane_change_state != log.LaneChangeState.off
        })
        
        return conditions
        
        # Apply standalone mode modifications
        if alcc_conditions.get("alcc_standalone_active", False):
            # For standalone operation, ensure we have steering control
            if final_desire == log.Desire.none and alcc_conditions["alcc_mode"] != ALCCMode.DISABLED:
                # Provide basic lane keeping when no other desire is active
                final_desire = log.Desire.laneKeep
            
            # Reduce aggressiveness for standalone operation
            safety_factor = 0.8 if alcc_conditions["alcc_mode"] == ALCCMode.STEERING_ONLY else 1.0
            final_params["lateral_accel"] = final_params.get("lateral_accel", 0.0) * safety_factor
            final_params["curvature"] = final_params.get("curvature", 0.0) * safety_factor
        
        # Apply brake response modifications
        if alcc_conditions.get("brake_response_active", False):
            if self.brake_response_mode == BrakeResponseMode.PAUSE:
                # Reduce control during braking
                final_params["lateral_accel"] = final_params.get("lateral_accel", 0.0) * 0.5
                final_params["curvature"] = final_params.get("curvature", 0.0) * 0.5
                final_params["confidence"] = final_params.get("confidence", 1.0) * 0.7
            elif self.brake_response_mode == BrakeResponseMode.DISENGAGE:
                # Complete disengagement
                final_desire = log.Desire.none
                final_params["lateral_accel"] = 0.0
                final_params["curvature"] = 0.0
                final_params["confidence"] = 0.0
        
        # Apply confidence threshold for ALCC
        alcc_confidence = alcc_conditions.get("alcc_confidence", 1.0)
        if final_params.get("confidence", 1.0) > alcc_confidence:
            final_params["confidence"] = alcc_confidence
        
        return final_desire, final_params
    
    def _check_alcc_eligibility(self, carstate, conditions: Dict[str, Any]) -> bool:
        """Check if ALCC should be active (control steering wheel)"""
        # Basic eligibility checks
        if conditions["standstill"] and not self.alcc_hold_at_standstill:
            return False
        
        if carstate.steerFaultTemporary or carstate.steerFaultPermanent:
            return False
        
        # Speed check - minimum speed for ALCC operation
        if carstate.vEgo < 3.0:  # ~11 km/h
            return False
        
        # Standard ALCC eligibility
        return True
    
    def _check_standalone_eligibility(self, carstate, conditions: Dict[str, Any]) -> bool:
        """Check if standalone operation (steering without cruise) should be active"""
        if not self.alcc_enabled:
            return False
        
        if self.alcc_mode == ALCCMode.DISABLED:
            return False
        
        # For STEERING_ONLY mode: no cruise control needed
        if self.alcc_mode == ALCCMode.STEERING_ONLY:
            return not carstate.cruiseState.enabled and carstate.vEgo > 5.0
        
        # For FULL_CONTROL mode: always available when ALCC is active
        if self.alcc_mode == ALCCMode.FULL_CONTROL:
            return not carstate.cruiseState.enabled
        
        return False
    
    def _apply_brake_response_logic(self, carstate, conditions: Dict[str, Any], lat_allowed: bool) -> Dict[str, Any]:
        """Apply brake response mode logic"""
        brake_pressed = carstate.brakePressed
        
        # Detect brake press transitions
        if brake_pressed and not self.brake_pressed_prev:
            self.brake_response_active = True
        elif not brake_pressed and self.brake_pressed_prev:
            self.brake_response_active = False
        
        self.brake_pressed_prev = brake_pressed
        
        # Apply brake response mode
        if self.brake_response_active:
            if self.brake_response_mode == BrakeResponseMode.PAUSE:
                # Temporarily pause lateral control
                conditions["alcc_modified_lat_allowed"] = False
                conditions["alcc_reason"] = "Brake response: PAUSED"
            elif self.brake_response_mode == BrakeResponseMode.DISENGAGE:
                # Completely disengage during braking
                conditions["alcc_modified_lat_allowed"] = False
                conditions["alcc_active"] = False
                conditions["alcc_reason"] = "Brake response: DISENGAGED"
            # MAINTAIN mode: no changes needed (default behavior)
        
        return conditions
    
    def _get_alcc_reason(self, carstate, base_conditions: Dict[str, Any]) -> str:
        """Get human-readable reason for ALCC state"""
        if base_conditions["standstill"] and not self.alcc_hold_at_standstill:
            return "Vehicle at standstill"
        
        if carstate.vEgo < 3.0:
            return "Speed too low"
        
        if carstate.steerFaultTemporary or carstate.steerFaultPermanent:
            return "Steering fault detected"
        
        if self.alcc_mode == ALCCMode.STEERING_ONLY:
            if carstate.cruiseState.enabled:
                return "Cruise active (STEERING_ONLY mode)"
            else:
                return "Standalone steering active"
        
        if self.alcc_mode == ALCCMode.FULL_CONTROL:
            if carstate.cruiseState.enabled:
                return "Cruise active (FULL_CONTROL mode)"
            else:
                return "Full lateral control active"
        
        return "ALCC active"
    
    def _assess_model_conditions(self, model_v2, v_ego: float) -> Dict[str, Any]:
        """Assess lane and road conditions from model data"""
        # Lane line confidence assessment
        lane_line_probs = []
        for i in range(min(4, len(model_v2.laneLines))):
            lane_line = model_v2.laneLines[i]
            if hasattr(lane_line, 'prob'):
                lane_line_probs.append(lane_line.prob)
            else:
                lane_line_probs.append(0.0)
        
        # Calculate confidence metrics
        left_confidence = (lane_line_probs[0] + lane_line_probs[1]) / 2 if len(lane_line_probs) >= 2 else 0.0
        right_confidence = (lane_line_probs[2] + lane_line_probs[3]) / 2 if len(lane_line_probs) >= 4 else 0.0
        overall_lane_confidence = (left_confidence + right_confidence) / 2
        
        # Calculate lane widths
        lane_width_left = 0.0
        lane_width_right = 0.0
        
        try:
            if len(model_v2.laneLines) >= 4 and len(model_v2.roadEdges) >= 2:
                lane_width_left = self._calculate_lane_width(model_v2.laneLines[0], model_v2.laneLines[1], model_v2.roadEdges[0])
                lane_width_right = self._calculate_lane_width(model_v2.laneLines[3], model_v2.laneLines[2], model_v2.roadEdges[1])
        except Exception as e:
            cloudlog.debug(f"Lane width calculation error: {e}")
        
        # Road edge availability
        road_edge_available = len(model_v2.roadEdges) >= 2
        
        # Determine reason for assessment
        if overall_lane_confidence < 0.3:
            reason = "Low lane line confidence"
        elif lane_width_left < self.min_lane_width and lane_width_right < self.min_lane_width:
            reason = "Narrow or unclear lanes"
        elif not road_edge_available:
            reason = "No road edge data available"
        else:
            reason = "Good lane conditions"
        
        return {
            "laneful_confidence": overall_lane_confidence,
            "laneless_confidence": max(0.3, 1.0 - overall_lane_confidence),  # Inverse relationship
            "lane_width_left": lane_width_left,
            "lane_width_right": lane_width_right,
            "road_edge_available": road_edge_available,
            "lane_line_quality": overall_lane_confidence,
            "reason": reason
        }
    
    def _assess_weather_conditions(self, weather_data) -> Dict[str, Any]:
        """Assess weather impact on lane detection"""
        # Simplified weather assessment
        weather_factor = 1.0  # Default no impact
        
        # Check for precipitation
        if hasattr(weather_data, 'precipitation') and weather_data.precipitation > 0:
            weather_factor *= 0.8  # 20% reduction in rain
        
        # Check for visibility
        if hasattr(weather_data, 'visibility') and weather_data.visibility < 1000:
            weather_factor *= 0.7  # 30% reduction in poor visibility
        
        return {
            "weather_factor": weather_factor,
            "weather_impact": "Clear" if weather_factor > 0.9 else "Moderate" if weather_factor > 0.7 else "Severe"
        }
    
    def _assess_road_curvature(self, model_v2, v_ego: float) -> Dict[str, Any]:
        """Assess road curvature for adaptive behavior"""
        try:
            if hasattr(model_v2, 'orientationRate') and len(model_v2.orientationRate.z) > 0:
                # Simple curvature estimation
                curvature = abs(model_v2.orientationRate.z[0]) * 2.0
                curvature_factor = min(1.0, max(0.5, 1.0 - curvature / 0.1))
                
                return {
                    "road_curvature": curvature,
                    "curvature_factor": curvature_factor,
                    "high_curvature": curvature > 0.05
                }
        except Exception:
            pass
        
        return {
            "road_curvature": 0.0,
            "curvature_factor": 1.0,
            "high_curvature": False
        }
    
    def _determine_optimal_mode(self, conditions: Dict[str, Any]) -> DLPMode:
        """Determine the optimal operating mode based on conditions including LCA user intention"""
        v_ego = conditions["v_ego"]
        lat_allowed = conditions["lat_allowed"]
        standstill = conditions["standstill"]
        
        # Basic eligibility checks
        if not lat_allowed or standstill:
            return DLPMode.LANEFUL
        
        # Check for LCA user intention (new user intention level)
        lca_intention = conditions.get("lca_user_intention", LCUserIntention.NONE)
        if lca_intention in [LCUserIntention.LEFT_CONFIRMED, LCUserIntention.RIGHT_CONFIRMED]:
            # User has confirmed lane change intention - force LCA mode
            return DLPMode.LCA
        elif lca_intention in [LCUserIntention.LEFT_EXECUTING, LCUserIntention.RIGHT_EXECUTING]:
            # Lane change is executing - stay in LCA mode
            return DLPMode.LCA
        elif lca_intention in [LCUserIntention.LEFT_OVERRIDE, LCUserIntention.RIGHT_OVERRIDE]:
            # User is overriding - return to safe base mode
            return DLPMode.LANEFUL
        
        # Legacy lane change detection (for backward compatibility)
        if self._is_lane_change_active(conditions):
            return DLPMode.LCA
        
        # Determine between laneful and laneless
        return self._determine_lane_mode(conditions, v_ego)
    
    def _is_lane_change_active(self, conditions: Dict[str, Any]) -> bool:
        """Check if lane change is currently active or requested"""
        # Check current state
        if self.lane_change_state != log.LaneChangeState.off:
            return True
        
        # Check for new lane change request
        if (conditions.get("left_blinker", False) or conditions.get("right_blinker", False)) and \
           conditions["v_ego"] > self.lca_min_speed:
            return True
        
        return False
    
    def _determine_lane_mode(self, conditions: Dict[str, Any], v_ego: float) -> DLPMode:
        """Determine between laneful and laneless modes"""
        if not self.laneless_enabled:
            return DLPMode.LANEFUL
        
        # Speed consideration - laneless more beneficial at lower speeds
        speed_factor = max(0.0, min(1.0, (self.laneless_max_speed - v_ego) / 20.0))
        
        # Lane confidence consideration
        laneful_confidence = conditions["laneful_confidence"]
        laneless_confidence = conditions["laneless_confidence"]
        
        # Weather factor
        weather_factor = conditions.get("weather_factor", 1.0)
        
        # Calculate laneless desirability
        laneless_score = (
            (1.0 - laneful_confidence) * 0.6 +  # Primary factor: lane quality
            speed_factor * 0.25 +               # Secondary factor: speed
            (1.0 - weather_factor) * 0.1 +      # Weather impact
            (0.1 if conditions["road_edge_available"] else 0.0)  # Road edge bonus
        )
        
        # Decision with hysteresis to prevent chattering
        if self.current_mode == DLPMode.LANELESS:
            # Higher threshold to stay in laneless (prevents rapid switching)
            threshold = self.laneless_min_confidence + 0.1
        else:
            # Lower threshold to enter laneless
            threshold = self.laneless_min_confidence
        
        return DLPMode.LANELESS if laneless_score > threshold else DLPMode.LANEFUL
    
    def _handle_mode_transition(self, target_mode: DLPMode, conditions: Dict[str, Any]) -> None:
        """Handle smooth transitions between modes"""
        if target_mode != self.current_mode:
            self.mode_transitions += 1
            self.mode_history.append({
                "from": self.current_mode,
                "to": target_mode,
                "timestamp": conditions["timestamp"],
                "reason": conditions["reason"]
            })
            cloudlog.info(f"DLP mode transition: {self.current_mode.name} -> {target_mode.name}, reason: {conditions['reason']}")
        
        # Update transition progress
        target_progress = 1.0 if target_mode == DLPMode.LANELESS else 0.0
        transition_rate = DT_MDL / self.laneless_transition_time
        
        if self.mode_transition_target != target_progress:
            self.mode_transition_target = target_progress
        
        # Smooth transition with filtering
        self.mode_transition_progress = calculate_transition_progress(
            self.mode_transition_progress,
            target_progress,
            transition_rate
        )
        
        # Update current mode when transition complete
        if abs(self.mode_transition_progress - target_progress) < 0.05:
            self.current_mode = target_mode
    
    def _execute_laneful_mode(self, conditions: Dict[str, Any]) -> Tuple[int, Dict[str, float]]:
        """Execute traditional lane line based control"""
        # Standard lane keeping logic
        desire = log.Desire.none
        
        # Use standard lane keeping with enhanced parameters
        lateral_accel = self.max_lateral_accel * self.mode_transition_progress
        curvature = 0.0  # Use standard lane detection
        
        return desire, {
            "lateral_accel": lateral_accel,
            "curvature": curvature,
            "mode_factor": self.mode_transition_progress
        }
    
    def _execute_laneless_mode(self, conditions: Dict[str, Any]) -> Tuple[int, Dict[str, float]]:
        """Execute laneless control using road edges and path prediction"""
        # Laneless specific logic
        desire = log.Desire.none
        
        # Reduce lateral acceleration for safety in laneless mode
        laneless_factor = 1.0 - (1.0 - self.laneless_lateral_factor) * (1.0 - self.mode_transition_progress)
        lateral_accel = self.max_lateral_accel * laneless_factor
        
        # Use road edges for basic lane positioning
        curvature = self._calculate_laneless_curvature(conditions)
        
        return desire, {
            "lateral_accel": lateral_accel,
            "curvature": curvature,
            "mode_factor": laneless_factor
        }
    
    def _execute_lca_mode(self, conditions: Dict[str, Any]) -> Tuple[int, Dict[str, float]]:
        """Execute lane change assistance with advanced features"""
        # LCA state machine logic
        v_ego = conditions["v_ego"]
        one_blinker = conditions.get("left_blinker", False) or conditions.get("right_blinker", False)
        
        # Execute LCA state machine
        desire = self._execute_lca_state_machine(conditions, v_ego, one_blinker)
        
        # Calculate LCA-specific control parameters
        control_params = self._calculate_lca_parameters(conditions)
        
        return desire, control_params
    
    def _execute_lca_state_machine(self, conditions: Dict[str, Any], v_ego: float, one_blinker: bool) -> int:
        """Execute the lane change assistance state machine"""
        below_lca_speed = v_ego < self.lca_min_speed
        
        # State machine logic
        if not one_blinker or below_lca_speed or self.lane_change_timer > 10.0:
            self.lane_change_state = log.LaneChangeState.off
            self.lane_change_direction = log.LaneChangeDirection.none
        else:
            # LaneChangeState.off
            if self.lane_change_state == log.LaneChangeState.off and one_blinker and not self.prev_one_blinker:
                self.lane_change_state = log.LaneChangeState.preLaneChange
                self.lane_change_ll_prob = 1.0
                self.lane_change_wait_timer = 0.0
                self.lane_change_completed = False
            
            # LaneChangeState.preLaneChange
            elif self.lane_change_state == log.LaneChangeState.preLaneChange:
                self.lane_change_wait_timer += DT_MDL
                
                # Set direction
                self.lane_change_direction = log.LaneChangeDirection.left if conditions.get("left_blinker", False) else log.LaneChangeDirection.right
                
                # Check for torque application or auto lane change
                torque_applied = self._check_torque_application(conditions)
                
                # Check blind spots
                blindspot_detected = self._check_blindspots(conditions)
                
                # Auto lane change logic
                if self.lca_delay > 0 and not torque_applied:
                    if blindspot_detected and self.lca_bsm_delay:
                        self.lane_change_wait_timer = 0.0  # Reset timer if BSM active
                    elif self.lane_change_wait_timer >= self.lca_delay:
                        # Enhanced auto lane change with laneless consideration
                        if self.current_mode == DLPMode.LANELESS and self.confidence < 0.7:
                            # Require higher confidence for auto lane change in laneless mode
                            pass  # Don't auto-trigger
                        else:
                            torque_applied = True
                
                # State transitions
                if not one_blinker or below_lca_speed:
                    self.lane_change_state = log.LaneChangeState.off
                    self.lane_change_direction = log.LaneChangeDirection.none
                elif torque_applied and not blindspot_detected:
                    self.lane_change_state = log.LaneChangeState.laneChangeStarting
                    self.lane_change_completed = True
                    self.lane_change_wait_timer = 0.0
            
            # LaneChangeState.laneChangeStarting
            elif self.lane_change_state == log.LaneChangeState.laneChangeStarting:
                # Fade out lane lines
                fade_rate = 2.0 * DT_MDL
                if self.current_mode == DLPMode.LANELESS:
                    fade_rate = 1.5 * DT_MDL  # Slower fade in laneless mode
                
                self.lane_change_ll_prob = max(self.lane_change_ll_prob - fade_rate, 0.0)
                
                # Check for completion
                if self.lane_change_ll_prob < 0.01:  # 99% certainty
                    self.lane_change_state = log.LaneChangeState.laneChangeFinishing
            
            # LaneChangeState.laneChangeFinishing
            elif self.lane_change_state == log.LaneChangeState.laneChangeFinishing:
                # Fade in lane lines
                fade_rate = DT_MDL
                if self.current_mode == DLPMode.LANELESS:
                    fade_rate = 0.7 * DT_MDL  # Slower fade in laneless mode
                
                self.lane_change_ll_prob = min(self.lane_change_ll_prob + fade_rate, 1.0)
                
                # Check for completion
                if self.lane_change_ll_prob > 0.99:
                    self.lane_change_direction = log.LaneChangeDirection.none
                    if one_blinker:
                        self.lane_change_state = log.LaneChangeState.preLaneChange
                    else:
                        self.lane_change_state = log.LaneChangeState.off
        
        # Update timer
        if self.lane_change_state in (log.LaneChangeState.off, log.LaneChangeState.preLaneChange):
            self.lane_change_timer = 0.0
        else:
            self.lane_change_timer += DT_MDL
        
        self.prev_one_blinker = one_blinker
        
        # Return appropriate desire
        return DESIRES[self.lane_change_direction][self.lane_change_state]
    
    def _check_torque_application(self, conditions: Dict[str, Any]) -> bool:
        """Check if driver has applied steering torque for lane change"""
        if not conditions.get("steering_pressed", False):
            return False
        
        steering_torque = conditions.get("steering_torque", 0.0)
        
        if self.lane_change_direction == log.LaneChangeDirection.left:
            return steering_torque > 0.5  # Small threshold for detection
        else:
            return steering_torque < -0.5
        
    def _check_blindspots(self, conditions: Dict[str, Any]) -> bool:
        """Check if blind spots are clear for lane change"""
        if self.lane_change_direction == log.LaneChangeDirection.left:
            return conditions.get("left_blindspot", False)
        else:
            return conditions.get("right_blindspot", False)
    
    def _calculate_lca_parameters(self, conditions: Dict[str, Any]) -> Dict[str, float]:
        """Calculate LCA-specific control parameters"""
        # Base parameters
        lateral_accel = self.max_lateral_accel
        curvature = 0.0
        
        # Adjust based on conditions
        if conditions.get("high_curvature", False):
            lateral_accel *= 0.8  # Reduce in curves
        
        # Mode-specific adjustments
        if self.current_mode == DLPMode.LANELESS:
            lateral_accel *= 0.9  # Extra caution in laneless mode
        
        return {
            "lateral_accel": lateral_accel,
            "curvature": curvature,
            "lca_active": True,
            "mode_factor": 1.0
        }
    
    def _calculate_lane_width(self, outer_lane, inner_lane, road_edge=None) -> float:
        """Calculate lane width from lane lines and road edges"""
        try:
            if not hasattr(inner_lane, 'x') or not hasattr(inner_lane, 'y'):
                return 0.0
            
            # Get coordinates
            x_coords = np.array(inner_lane.x)
            y_inner = np.array(inner_lane.y)
            
            # Interpolate outer lane
            if hasattr(outer_lane, 'x') and hasattr(outer_lane, 'y'):
                y_outer = np.interp(x_coords, np.array(outer_lane.x), np.array(outer_lane.y))
                distance = np.mean(np.abs(y_outer - y_inner))
                
                # Validate with road edge if available
                if road_edge is not None and hasattr(road_edge, 'x') and hasattr(road_edge, 'y'):
                    y_edge = np.interp(x_coords, np.array(road_edge.x), np.array(road_edge.y))
                    edge_distance = np.mean(np.abs(y_edge - y_inner))
                    
                    # Sanity check: road edge should be farther than lane line
                    if edge_distance < distance * 0.8:
                        return 0.0  # Invalid detection
                
                return float(distance)
        except Exception as e:
            cloudlog.debug(f"Lane width calculation error: {e}")
        
        return 0.0
    
    def _calculate_laneless_curvature(self, conditions: Dict[str, Any]) -> float:
        """Calculate curvature for laneless mode"""
        # Simplified curvature calculation for laneless
        # Use road edges and path prediction
        return 0.0  # Placeholder - would use more sophisticated logic
    
    def _update_metrics(self, conditions: Dict[str, Any], start_time: float) -> None:
        """Update performance and usage metrics"""
        # Update mode durations
        if self.current_mode in self.mode_durations:
            self.mode_durations[self.current_mode] += DT_MDL
        
        # Update average confidence
        self.avg_confidence = 0.95 * self.avg_confidence + 0.05 * self.confidence
        
        # Track performance
        update_time = time.time() - start_time
        if update_time > 0.01:  # Log slow updates
            cloudlog.debug(f"DLP update took {update_time*1000:.1f}ms")
    
    def _build_status(self, conditions: Dict[str, Any], desire: int, control_params: Dict[str, float]) -> DLPStatus:
        """Build comprehensive status object"""
        return DLPStatus(
            mode=self.current_mode,
            lca_mode=self.current_lca_mode,
            desire=desire,
            confidence=self.confidence,
            active=self.current_mode != DLPMode.LANEFUL or self.lane_change_state != log.LaneChangeState.off,
            available=self.enabled and conditions["lat_allowed"],
            reason=conditions["reason"],
            lateral_accel=control_params["lateral_accel"],
            target_curvature=control_params["curvature"],
            lane_width_left=self.lane_width_left,
            lane_width_right=self.lane_width_right,
            blindspot_clear=self.blindspot_clear,
            lane_change_ready=self.lane_change_ready
        )
    
    def _build_status_with_lca(self, lca_conditions: Dict[str, Any], desire: int, control_params: Dict[str, float]) -> DLPStatus:
        """Build comprehensive status object including both ALCC and LCA information"""
        # Use the base status building logic
        base_status = self._build_status(lca_conditions, desire, control_params)
        
        # Get ALCC information
        alcc_active = lca_conditions.get("alcc_active", False)
        alcc_standalone = lca_conditions.get("alcc_standalone_active", False)
        alcc_reason = lca_conditions.get("alcc_reason", base_status.reason)
        
        # Get LCA information
        lca_intention = lca_conditions.get("lca_user_intention", LCUserIntention.NONE)
        lca_active = lca_conditions.get("lca_active", False)
        lca_reason = lca_conditions.get("lca_reason", base_status.reason)
        
        # Determine final active state and reason
        if alcc_active or lca_active:
            # Force active state when either ALCC or LCA is controlling
            active = True
            available = lca_conditions.get("alcc_modified_lat_allowed", base_status.available)
            
            # Build comprehensive reason
            if alcc_active and lca_active:
                reason = f"ALCC+{lca_intention.name}"
            elif alcc_active:
                reason = alcc_reason
                if alcc_standalone:
                    reason = f"ALCC Standalone: {self.alcc_mode.name}"
            elif lca_active:
                reason = lca_reason
            else:
                reason = base_status.reason
        else:
            # Use base status when neither is active
            active = base_status.active
            available = base_status.available
            reason = base_status.reason
        
        # Return enhanced status with both ALCC and LCA information
        return DLPStatus(
            mode=base_status.mode,
            lca_mode=base_status.lca_mode,
            desire=base_status.desire,
            confidence=base_status.confidence,
            active=active,
            available=available,
            reason=reason,
            lateral_accel=base_status.lateral_accel,
            target_curvature=base_status.target_curvature,
            lane_width_left=base_status.lane_width_left,
            lane_width_right=base_status.lane_width_right,
            blindspot_clear=base_status.blindspot_clear,
            lane_change_ready=base_status.lane_change_ready
        )
    
    def _get_disabled_status(self) -> DLPStatus:
        """Get status when controller is disabled"""
        # Reset ALCC state when disabled
        self.alcc_active = False
        self.alcc_standalone_active = False
        self.brake_response_active = False
        self.emergency_active = False
        
        # Reset LCA user intention state when disabled
        self.lca_user_intention = LCUserIntention.NONE
        self.lca_user_confirmed = False
        self.lca_user_override = False
        self.lca_intention_timer = 0.0
        self.lca_confirmation_timer = 0.0
        
        return DLPStatus(
            mode=DLPMode.LANEFUL,
            lca_mode=LCAMode.OFF,
            desire=log.Desire.none,
            confidence=0.0,
            active=False,
            available=False,
            reason="Controller disabled",
            lateral_accel=0.0,
            target_curvature=0.0,
            lane_width_left=0.0,
            lane_width_right=0.0,
            blindspot_clear=True,
            lane_change_ready=False
        )
    
    def is_alcc_active(self) -> bool:
        """Check if ALCC is currently active (controlling steering)"""
        return self.alcc_active
    
    def is_alcc_standalone_active(self) -> bool:
        """Check if ALCC standalone mode is active (steering without cruise)"""
        return self.alcc_standalone_active
    
    def get_alcc_mode(self) -> ALCCMode:
        """Get current ALCC mode"""
        return self.alcc_mode
    
    def set_alcc_mode(self, mode: ALCCMode):
        """Set ALCC mode"""
        self.alcc_mode = mode
        cloudlog.info(f"ALCC mode changed to {mode.name}")
    
    def get_alcc_status(self) -> Dict[str, Any]:
        """Get comprehensive ALCC status"""
        return {
            "enabled": self.alcc_enabled,
            "mode": self.alcc_mode.name,
            "active": self.alcc_active,
            "standalone_active": self.alcc_standalone_active,
            "brake_response_mode": self.brake_response_mode.name,
            "brake_response_active": self.brake_response_active,
            "emergency_active": self.emergency_active
        }
    
    def is_lca_intention_active(self) -> bool:
        """Check if LCA user intention is currently active"""
        return self.lca_user_intention in [LCUserIntention.LEFT_CONFIRMED, LCUserIntention.RIGHT_CONFIRMED, LCUserIntention.LEFT_EXECUTING, LCUserIntention.RIGHT_EXECUTING]
    
    def get_lca_intention(self) -> LCUserIntention:
        """Get current LCA user intention"""
        return self.lca_user_intention
    
    def get_lca_intention_status(self) -> Dict[str, Any]:
        """Get comprehensive LCA user intention status"""
        return {
            "enabled": self.lca_intention_enabled,
            "intention": self.lca_user_intention.name,
            "confirmed": self.lca_user_confirmed,
            "override": self.lca_user_override,
            "direction": self._get_lca_direction(self.lca_user_intention),
            "torque_threshold": self.lca_torque_threshold,
            "auto_confirm_time": self.lca_auto_confirm_time,
            "override_torque": self.lca_override_torque,
            "confidence_threshold": self.lca_confidence_threshold
        }
    
    def reset_lca_intention(self):
        """Reset LCA user intention to NONE state"""
        self.lca_user_intention = LCUserIntention.NONE
        self.lca_user_confirmed = False
        self.lca_user_override = False
        self.lca_intention_timer = 0.0
        self.lca_confirmation_timer = 0.0
        cloudlog.info("LCA user intention reset to NONE")
    
    def _refresh_params_if_needed(self) -> None:
        """Refresh parameters if enough time has passed"""
        now = time.monotonic()
        if now - self.last_param_refresh > self.param_refresh_interval:
            self.last_param_refresh = now
            self._load_parameters()
    
    # Public API methods
    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable DLP controller"""
        if self.enabled != enabled:
            self.enabled = enabled
            self.params.put_bool("np_dlp_enable", enabled)
            cloudlog.info(f"DLP controller {'enabled' if enabled else 'disabled'}")
    
    def get_mode(self) -> str:
        """Get current operating mode"""
        return self.current_mode.name.lower()
    
    def get_desire(self) -> int:
        """Get current desire value (backward compatibility)"""
        return self.desire
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get comprehensive health status for monitoring"""
        return {
            "enabled": self.enabled,
            "current_mode": self.current_mode.name,
            "lca_mode": self.current_lca_mode.name,
            "confidence": self.confidence,
            "active": self.current_mode != DLPMode.LANEFUL or self.lane_change_state != log.LaneChangeState.off,
            "available": self.enabled,
            "mode_durations": {mode.name: duration for mode, duration in self.mode_durations.items()},
            "mode_transitions": self.mode_transitions,
            "lca_activations": self.lca_activations,
            "avg_confidence": self.avg_confidence,
            "lane_width_left": self.lane_width_left,
            "lane_width_right": self.lane_width_right,
            "blindspot_clear": self.blindspot_clear,
            "lane_change_ready": self.lane_change_ready
        }
    
    def get_debug_info(self) -> Dict[str, Any]:
        """Get detailed debug information"""
        return {
            "current_mode": self.current_mode.name,
            "lca_mode": self.current_lca_mode.name,
            "confidence": self.confidence,
            "mode_transition_progress": self.mode_transition_progress,
            "lane_change_state": self.lane_change_state,
            "lane_change_direction": self.lane_change_direction,
            "lane_width_left": self.lane_width_left,
            "lane_width_right": self.lane_width_right,
            "mode_durations": {mode.name: duration for mode, duration in self.mode_durations.items()},
            "mode_transitions": self.mode_transitions,
            "lca_activations": self.lca_activations,
            "enabled": self.enabled
        }
    
    def set_lca_mode(self, mode: str) -> None:
        """Set LCA mode (OFF, NUDGE, TIMED, ADAPTIVE)"""
        try:
            self.current_lca_mode = LCAMode[mode.upper()]
            self.lca_mode_setting = mode
            self.params.put("np_lca_mode", mode)
            cloudlog.info(f"LCA mode set to {mode}")
        except KeyError:
            cloudlog.error(f"Invalid LCA mode: {mode}")
    
    def active(self) -> bool:
        """Check if DLP is active and operational"""
        return self.enabled and (self.current_mode != DLPMode.LANEFUL or self.lane_change_state != log.LaneChangeState.off)


# Helper functions
def calculate_transition_progress(current: float, target: float, rate: float) -> float:
    """Calculate smooth transition progress"""
    if target > current:
        return min(target, current + rate)
    elif target < current:
        return max(target, current - rate)
    else:
        return current


# Constants and mappings
LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS  # 20 mph minimum for LCA
LANE_CHANGE_TIME_MAX = 10.0  # Maximum lane change duration

DESIRES = {
    log.LaneChangeDirection.none: {
        log.LaneChangeState.off: log.Desire.none,
        log.LaneChangeState.preLaneChange: log.Desire.none,
        log.LaneChangeState.laneChangeStarting: log.Desire.none,
        log.LaneChangeState.laneChangeFinishing: log.Desire.none,
    },
    log.LaneChangeDirection.left: {
        log.LaneChangeState.off: log.Desire.none,
        log.LaneChangeState.preLaneChange: log.Desire.none,
        log.LaneChangeState.laneChangeStarting: log.Desire.laneChangeLeft,
        log.LaneChangeState.laneChangeFinishing: log.Desire.laneChangeLeft,
    },
    log.LaneChangeDirection.right: {
        log.LaneChangeState.off: log.Desire.none,
        log.LaneChangeState.preLaneChange: log.Desire.none,
        log.LaneChangeState.laneChangeStarting: log.Desire.laneChangeRight,
        log.LaneChangeState.laneChangeFinishing: log.Desire.laneChangeRight,
    },
}
