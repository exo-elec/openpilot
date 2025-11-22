#!/usr/bin/env python3
"""
NagasPilot Dynamic Experimental Mode Controller - Hybrid DEM implementation
Combines sunnypilot's sophisticated filtering with NagasPilot's efficiency patterns

Key features:
- Efficient Kalman-style filtering without full matrix operations
- Confidence-based decision making with NagasPilot patterns
- Comprehensive health monitoring and debug reporting
- Minimal computational overhead while maintaining sophistication
"""

import numpy as np
from enum import Enum, auto
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from collections import deque

from cereal import log, custom
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog


class DEMMode(Enum):
    """Dynamic Experimental Mode states"""
    ACC = auto()      # Adaptive Cruise Control
    BLENDED = auto()  # Enhanced experimental mode


class DEMScenario(Enum):
    """Scenario classification for decision making"""
    NORMAL = auto()
    CRITICAL_TTC = auto()
    HARD_BRAKE = auto()
    CUT_IN = auto()
    LOW_SPEED = auto()
    STOP_PREDICTION = auto()
    HIGH_CURVATURE = auto()
    PLANNER_BRAKING = auto()
    GAS_DISENGAGE = auto()
    HIGHWAY_CRUISE = auto()
    STABLE_FOLLOWING = auto()
    LEAD_ABSENCE = auto()


@dataclass
class DEMHealthStatus:
    """Comprehensive health monitoring for DEM"""
    filter_health: float = 1.0
    decision_confidence: float = 1.0
    scenario_stability: float = 1.0
    sensor_quality: float = 1.0
    
    def get_overall_health(self) -> float:
        """Calculate overall health score"""
        return (self.filter_health * self.decision_confidence * 
                self.scenario_stability * self.sensor_quality) ** 0.25
    
    def is_healthy(self) -> bool:
        """Check if system is healthy for operation"""
        return self.get_overall_health() > 0.7


class EfficientKalmanFilter:
    """
    Efficient Kalman-style filter without full matrix operations
    Provides 80% of Kalman benefits with 20% computational cost
    """
    
    def __init__(self, initial_value: float = 0.0, process_noise: float = 0.01,
                 measurement_noise: float = 0.1, adaptive: bool = True):
        self.x = float(initial_value)  # State estimate
        self.P = 1.0  # Error covariance (scalar for efficiency)
        self.Q = process_noise  # Process noise
        self.R = measurement_noise  # Measurement noise
        self.adaptive = adaptive
        
        # Confidence tracking
        self.confidence = 1.0
        self.measurement_history = deque(maxlen=10)
        self.innovation_history = deque(maxlen=10)
        
    def update(self, measurement: float) -> Tuple[float, float]:
        """
        Update filter with new measurement
        Returns: (filtered_value, confidence)
        """
        # Prediction step
        self.P += self.Q
        
        # Innovation (measurement residual)
        innovation = measurement - self.x
        self.innovation_history.append(abs(innovation))
        
        # Adaptive noise adjustment based on innovation history
        if self.adaptive and len(self.innovation_history) >= 3:
            avg_innovation = np.mean(self.innovation_history)
            if avg_innovation > 2.0:  # High innovation = more measurement noise
                self.R = min(self.R * 1.1, 2.0)
            elif avg_innovation < 0.5:  # Low innovation = less measurement noise
                self.R = max(self.R * 0.9, 0.05)
        
        # Innovation covariance
        S = self.P + self.R
        
        # Kalman gain
        K = self.P / S if S > 1e-6 else 0.0
        
        # Update state
        self.x += K * innovation
        
        # Update covariance
        self.P = (1 - K) * self.P
        
        # Update confidence based on innovation and measurement consistency
        self.measurement_history.append(measurement)
        if len(self.measurement_history) >= 5:
            measurement_variance = np.var(self.measurement_history)
            confidence_factor = 1.0 / (1.0 + measurement_variance)
            self.confidence = 0.9 * self.confidence + 0.1 * confidence_factor
        
        return self.x, self.confidence
    
    def reset(self, value: float):
        """Reset filter to specific value"""
        self.x = float(value)
        self.P = 1.0
        self.confidence = 1.0
        self.measurement_history.clear()
        self.innovation_history.clear()


class ModeTransitionManager:
    """Enhanced mode transition management with NagasPilot patterns"""
    
    def __init__(self):
        self.current_mode = DEMMode.ACC
        self.mode_history = deque(maxlen=20)  # 1 second history
        self.transition_count = 0
        self.last_transition_time = 0.0
        self.emergency_override = False
        
        # Confidence-based transition thresholds
        self.confidence_threshold = 0.7
        self.emergency_confidence_threshold = 0.5
        
        # Hysteresis parameters
        self.min_mode_duration = 15  # frames (0.75s at 20Hz)
        self.current_mode_frames = 0
        
    def should_transition(self, suggested_mode: DEMMode, confidence: float,
                         emergency: bool = False) -> bool:
        """Determine if mode transition should occur"""
        self.current_mode_frames += 1
        current_time = time.time()

        # Emergency override logic
        if emergency and confidence > self.emergency_confidence_threshold:
            if not self.emergency_override:
                cloudlog.info(f"DEM emergency transition to {suggested_mode}")
                self.emergency_override = True
            return True
        
        # Reset emergency override after stable period
        if self.emergency_override and self.current_mode_frames > self.min_mode_duration:
            self.emergency_override = False

        # Same mode - no transition needed
        if suggested_mode == self.current_mode:
            return False

        # Check minimum duration requirement
        if self.current_mode_frames < self.min_mode_duration and not emergency:
            return False
        
        # Confidence-based decision
        if confidence < self.confidence_threshold:
            return False
        
        # Record transition
        self.mode_history.append({
            'from': self.current_mode,
            'to': suggested_mode,
            'confidence': confidence,
            'emergency': emergency,
            'time': current_time
        })

        return True

    def transition_to(self, new_mode: DEMMode):
        """Execute mode transition"""
        old_mode = self.current_mode
        self.current_mode = new_mode
        self.current_mode_frames = 0
        self.transition_count += 1
        self.last_transition_time = time.time()

        cloudlog.info(f"DEM mode transition: {old_mode.name} -> {new_mode.name}")
    
    def get_stability_score(self) -> float:
        """Calculate mode stability score (1.0 = perfectly stable)"""
        if len(self.mode_history) < 5:
            return 1.0
        
        recent_transitions = len([h for h in self.mode_history if h['to'] != h['from']])
        stability = 1.0 - (recent_transitions / len(self.mode_history))
        return max(0.1, stability)


class NpDemController:
    # Flow overview:
    # 1. __init__ seeds all filters/state (Kalman filters, confidence history,
    #    mode transition manager, etc.).
    # 2. update() is called every controls tick; it reads SubMaster data,
    #    filters it, classifies the scenario, and asks the transition manager
    #    whether to switch modes.
    # 3. Helper methods break that pipeline into small pieces so each tuning
    #    change (e.g. TTC thresholds) lives in one place.
    """
    NagasPilot Dynamic Experimental Mode Controller
    Hybrid approach combining DEM sophistication with NagasPilot efficiency
    """
    
    # Configuration constants
    SPEED_THRESHOLD_HIGHWAY = 22.23  # m/s (80 kph)
    SPEED_THRESHOLD_CITY = 15.27     # m/s (55 kph)
    SPEED_THRESHOLD_LOW = 5.56       # m/s (20 kph)
    SPEED_THRESHOLD_CREEP = 2.23     # m/s (8 kph)
    
    LEAD_TTC_CRITICAL = 1.75         # seconds
    LEAD_TTC_CAUTION = 3.0           # seconds
    LEAD_DIST_VERY_CLOSE = 10.0      # meters
    LEAD_DIST_FAR_HIGHWAY = 85.0     # meters
    
    LEAD_ACCEL_HARD_BRAKE = -3.0     # m/s²
    LEAD_ACCEL_MILD_BRAKE = -2.0     # m/s²
    LEAD_ACCEL_PULLING_AWAY = 0.5    # m/s²
    
    STEERING_ANGLE_HIGH_CURVATURE = 45.0  # degrees
    
    def __init__(self, CP, params=None):
        self.CP = CP
        self.params = params or Params()
        self.enabled = False
        
        # Initialize components
        self.transition_manager = ModeTransitionManager()
        self.health_status = DEMHealthStatus()
        
        # Initialize efficient filters
        self._init_filters()
        
        # State tracking
        self.lead_id_prev = -1
        self.lead_absence_frames = 0
        self.personality = log.LongitudinalPersonality.standard
        
        # Debug and monitoring
        self.debug_mode = self.params.get_bool("DEMDebugMode")
        self.frame_count = 0
        self.decision_log = deque(maxlen=100)
        
        # Performance metrics
        self.avg_decision_time = 0.0
        self.confidence_history = deque(maxlen=50)
        
        cloudlog.info("NpDemController initialized")
    
    def _init_filters(self):
        """Initialize efficient Kalman-style filters"""
        # Vehicle state filters
        self.v_ego_filter = EfficientKalmanFilter(
            initial_value=0.0,
            process_noise=0.05,
            measurement_noise=0.2,
            adaptive=True
        )
        
        # Lead vehicle filters
        self.lead_drel_filter = EfficientKalmanFilter(
            initial_value=self.LEAD_DIST_FAR_HIGHWAY,
            process_noise=0.1,
            measurement_noise=1.0,
            adaptive=True
        )
        
        self.lead_vrel_filter = EfficientKalmanFilter(
            initial_value=0.0,
            process_noise=0.1,
            measurement_noise=0.5,
            adaptive=True
        )
        
        self.lead_arel_filter = EfficientKalmanFilter(
            initial_value=0.0,
            process_noise=0.05,
            measurement_noise=0.3,
            adaptive=True
        )
        
        # Steering filter
        self.steering_filter = EfficientKalmanFilter(
            initial_value=0.0,
            process_noise=0.1,
            measurement_noise=2.0,
            adaptive=True
        )
    
    def _calculate_ttc(self, d_lead: float, v_ego: float, v_lead: float) -> float:
        """Calculate time to collision"""
        relative_speed = v_ego - v_lead
        if d_lead > 0.1 and relative_speed > 0.3:
            return max(0.0, d_lead / relative_speed)
        return float('inf')
    
    def _classify_scenario(self, v_ego: float, d_lead: float, v_lead: float,
                          a_lead: float, ttc: float, steering_angle: float,
                          model_stop_intention: bool, allow_throttle: bool) -> Tuple[DEMScenario, float]:
        """
        Classify current driving scenario and return confidence
        Returns: (scenario, confidence)
        """
        scenarios = []
        
        # Critical scenarios (high priority)
        if ttc < self.LEAD_TTC_CRITICAL and v_ego > self.SPEED_THRESHOLD_LOW:
            scenarios.append((DEMScenario.CRITICAL_TTC, 0.9))
        
        if a_lead < self.LEAD_ACCEL_HARD_BRAKE and d_lead < (v_ego * 2.5):
            scenarios.append((DEMScenario.HARD_BRAKE, 0.85))
        
        # Moderate scenarios
        if v_ego < self.SPEED_THRESHOLD_LOW and d_lead < (self.LEAD_DIST_VERY_CLOSE * 1.8):
            scenarios.append((DEMScenario.LOW_SPEED, 0.7))
        
        if model_stop_intention and v_ego > self.SPEED_THRESHOLD_CREEP:
            scenarios.append((DEMScenario.STOP_PREDICTION, 0.75))
        
        if steering_angle > self.STEERING_ANGLE_HIGH_CURVATURE and v_ego < self.SPEED_THRESHOLD_CITY:
            scenarios.append((DEMScenario.HIGH_CURVATURE, 0.65))
        
        if not allow_throttle:
            scenarios.append((DEMScenario.GAS_DISENGAGE, 0.6))
        
        # Stable scenarios (ACC-favoring)
        if v_ego > self.SPEED_THRESHOLD_HIGHWAY:
            if d_lead > (self.LEAD_DIST_FAR_HIGHWAY * 0.8) or ttc > (self.LEAD_TTC_CAUTION * 1.5):
                scenarios.append((DEMScenario.HIGHWAY_CRUISE, 0.8))
        
        if ttc > self.LEAD_TTC_CAUTION and d_lead > (self.LEAD_DIST_VERY_CLOSE * 2.0):
            if abs(a_lead) < (self.LEAD_ACCEL_PULLING_AWAY * 0.8):
                scenarios.append((DEMScenario.STABLE_FOLLOWING, 0.75))
        
        if not any(s[0] in [DEMScenario.CRITICAL_TTC, DEMScenario.HARD_BRAKE] for s in scenarios):
            scenarios.append((DEMScenario.NORMAL, 0.5))
        
        # Return highest priority scenario
        if scenarios:
            # Sort by confidence, then by criticality
            scenarios.sort(key=lambda x: (x[1], self._get_scenario_priority(x[0])), reverse=True)
            return scenarios[0]
        
        return DEMScenario.NORMAL, 0.5
    
    def _get_scenario_priority(self, scenario: DEMScenario) -> int:
        """Get priority score for scenario (higher = more critical)"""
        priorities = {
            DEMScenario.CRITICAL_TTC: 10,
            DEMScenario.HARD_BRAKE: 9,
            DEMScenario.CUT_IN: 8,
            DEMScenario.STOP_PREDICTION: 7,
            DEMScenario.LOW_SPEED: 6,
            DEMScenario.HIGH_CURVATURE: 5,
            DEMScenario.GAS_DISENGAGE: 4,
            DEMScenario.NORMAL: 1,
            DEMScenario.STABLE_FOLLOWING: 2,
            DEMScenario.HIGHWAY_CRUISE: 3,
            DEMScenario.LEAD_ABSENCE: 2,
        }
        return priorities.get(scenario, 1)
    
    def _suggest_mode(self, scenario: DEMScenario, confidence: float,
                     v_ego: float, lead_status: bool) -> DEMMode:
        """Suggest mode based on scenario classification"""
        
        # Scenarios that require blended mode
        blended_scenarios = {
            DEMScenario.CRITICAL_TTC,
            DEMScenario.HARD_BRAKE,
            DEMScenario.CUT_IN,
            DEMScenario.LOW_SPEED,
            DEMScenario.STOP_PREDICTION,
            DEMScenario.HIGH_CURVATURE,
            DEMScenario.GAS_DISENGAGE
        }
        
        # Scenarios that favor ACC mode
        acc_scenarios = {
            DEMScenario.HIGHWAY_CRUISE,
            DEMScenario.STABLE_FOLLOWING,
            DEMScenario.LEAD_ABSENCE
        }
        
        if scenario in blended_scenarios:
            return DEMMode.BLENDED
        elif scenario in acc_scenarios:
            return DEMMode.ACC
        else:
            # For normal scenarios, use speed-based default
            return DEMMode.BLENDED if v_ego < self.SPEED_THRESHOLD_CITY and lead_status else DEMMode.ACC
    
    def update(self, sm):
        """Main update function - efficient implementation"""
        start_time = time.time()
        self.frame_count += 1
        
        if not self.enabled:
            return
        
        # Get raw sensor data
        v_ego_raw = sm['carState'].vEgo
        steer_angle_raw = sm['carState'].steeringAngleDeg - sm['liveParameters'].angleOffsetDeg
        standstill = sm['carState'].standstill
        
        # Update filters
        v_ego, v_ego_conf = self.v_ego_filter.update(v_ego_raw)
        steer_angle, steer_conf = self.steering_filter.update(abs(steer_angle_raw))
        
        # Process lead data
        lead_status = sm['radarState'].leadOne.status
        current_lead_id = sm['radarState'].leadOne.radarTrackId if lead_status else -1
        
        if lead_status:
            # Lead detected - update filters
            d_lead_raw = sm['radarState'].leadOne.dRel
            v_lead_raw = sm['radarState'].leadOne.vLead
            a_lead_raw = sm['radarState'].leadOne.aLeadK
            model_prob = sm['radarState'].leadOne.modelProb
            
            # Handle lead changes
            if current_lead_id != self.lead_id_prev and self.lead_id_prev != -1:
                # New lead - reset filters
                self.lead_drel_filter.reset(d_lead_raw)
                self.lead_vrel_filter.reset(v_lead_raw)
                self.lead_arel_filter.reset(a_lead_raw)
            
            # Update lead filters
            d_lead, d_lead_conf = self.lead_drel_filter.update(d_lead_raw)
            v_lead, v_lead_conf = self.lead_vrel_filter.update(v_lead_raw)
            a_lead, a_lead_conf = self.lead_arel_filter.update(a_lead_raw)
            
            self.lead_absence_frames = 0
        else:
            # No lead - decay to defaults
            d_lead, d_lead_conf = self.lead_drel_filter.update(self.LEAD_DIST_FAR_HIGHWAY)
            v_lead, v_lead_conf = self.lead_vrel_filter.update(v_ego)  # Match ego speed
            a_lead, a_lead_conf = self.lead_arel_filter.update(0.0)
            
            self.lead_absence_frames += 1
        
        self.lead_id_prev = current_lead_id
        
        # Calculate derived values
        ttc = self._calculate_ttc(d_lead, v_ego, v_lead)
        
        # Model stop intention (simplified)
        model_stop_intention = False
        if 'modelV2' in sm:
            model_v = sm['modelV2'].velocity.x
            if len(model_v) >= 5:
                avg_end_v = np.mean(model_v[-5:])
                model_stop_intention = avg_end_v < self.SPEED_THRESHOLD_CREEP
        
        # Throttle allowance
        allow_throttle = True  # Simplified - should come from planner
        
        # Classify scenario
        scenario, scenario_conf = self._classify_scenario(
            v_ego, d_lead, v_lead, a_lead, ttc, steer_angle,
            model_stop_intention, allow_throttle
        )
        
        # Calculate overall confidence
        overall_confidence = (v_ego_conf * d_lead_conf * v_lead_conf * 
                            a_lead_conf * steer_conf * scenario_conf) ** (1/6)
        
        # Suggest mode
        suggested_mode = self._suggest_mode(scenario, overall_confidence, v_ego, lead_status)
        
        # Check for emergency conditions
        emergency = (scenario == DEMScenario.CRITICAL_TTC or 
                    scenario == DEMScenario.HARD_BRAKE)
        
        # Mode transition decision
        if self.transition_manager.should_transition(suggested_mode, overall_confidence, emergency):
            self.transition_manager.transition_to(suggested_mode)
        
        # Update health status
        self._update_health_status(overall_confidence, scenario)
        
        # Log decision for debugging
        if self.debug_mode:
            decision_info = {
                'frame': self.frame_count,
                'scenario': scenario.name,
                'suggested_mode': suggested_mode.name,
                'current_mode': self.transition_manager.current_mode.name,
                'confidence': overall_confidence,
                'emergency': emergency,
                'v_ego': v_ego,
                'd_lead': d_lead if lead_status else None,
                'ttc': ttc if lead_status else None,
                'scenario_conf': scenario_conf
            }
            self.decision_log.append(decision_info)
            
            if self.frame_count % 100 == 0:  # Log every 5 seconds
                self._log_summary()
        
        # Update performance metrics
        decision_time = time.time() - start_time
        self.avg_decision_time = 0.9 * self.avg_decision_time + 0.1 * decision_time
        self.confidence_history.append(overall_confidence)
    
    def _update_health_status(self, confidence: float, scenario: DEMScenario):
        """Update comprehensive health status"""
        # Filter health based on confidence trend
        if len(self.confidence_history) >= 10:
            recent_confidence = list(self.confidence_history)[-10:]
            confidence_trend = np.std(recent_confidence)
            self.health_status.filter_health = max(0.1, 1.0 - confidence_trend)
        
        # Decision confidence
        self.health_status.decision_confidence = confidence
        
        # Scenario stability
        stability_score = self.transition_manager.get_stability_score()
        self.health_status.scenario_stability = stability_score
        
        # Sensor quality (simplified)
        self.health_status.sensor_quality = 0.95  # Could be enhanced with actual sensor diagnostics
    
    def _log_summary(self):
        """Log periodic summary for debugging"""
        if not self.decision_log:
            return
        
        recent_decisions = list(self.decision_log)[-20:]  # Last 20 decisions
        mode_counts = {}
        scenario_counts = {}
        avg_confidence = 0.0
        
        for decision in recent_decisions:
            mode = decision['current_mode']
            scenario = decision['scenario']
            confidence = decision['confidence']
            
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
            scenario_counts[scenario] = scenario_counts.get(scenario, 0) + 1
            avg_confidence += confidence
        
        avg_confidence /= len(recent_decisions)
        
        cloudlog.debug(f"DEM Summary - Mode: {mode_counts}, "
                      f"Scenarios: {scenario_counts}, "
                      f"Avg Confidence: {avg_confidence:.3f}, "
                      f"Health: {self.health_status.get_overall_health():.3f}")
    
    def get_mode(self) -> str:
        """Get current mode for MPC (acc/blended)"""
        return self.transition_manager.current_mode.name.lower()
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get comprehensive health status for monitoring"""
        return {
            'overall_health': self.health_status.get_overall_health(),
            'is_healthy': self.health_status.is_healthy(),
            'filter_health': self.health_status.filter_health,
            'decision_confidence': self.health_status.decision_confidence,
            'scenario_stability': self.health_status.scenario_stability,
            'sensor_quality': self.health_status.sensor_quality,
            'mode_stability': self.transition_manager.get_stability_score(),
            'transition_count': self.transition_manager.transition_count,
            'avg_decision_time_ms': self.avg_decision_time * 1000,
            'current_mode': self.transition_manager.current_mode.name,
            'enabled': self.enabled
        }
    
    def get_debug_info(self) -> Dict[str, Any]:
        """Get detailed debug information"""
        if not self.decision_log:
            return {'error': 'No decision history available'}
        
        latest = self.decision_log[-1]
        recent_modes = [d['current_mode'] for d in list(self.decision_log)[-10:]]
        
        return {
            'latest_decision': latest,
            'mode_history': recent_modes,
            'health_status': self.get_health_status(),
            'performance': {
                'avg_decision_time_ms': self.avg_decision_time * 1000,
                'frame_count': self.frame_count,
                'decision_log_size': len(self.decision_log)
            },
            'filter_status': {
                'v_ego_confidence': self.v_ego_filter.confidence,
                'lead_drel_confidence': self.lead_drel_filter.confidence if hasattr(self, 'lead_drel_filter') else 0.0,
                'steering_confidence': self.steering_filter.confidence
            }
        }
    
    def set_enabled(self, enabled: bool):
        """Enable/disable DEM controller"""
        if self.enabled != enabled:
            self.enabled = enabled
            cloudlog.info(f"NpDemController {'enabled' if enabled else 'disabled'}")
    
    def active(self) -> bool:
        """Check if DEM is active and enabled"""
        return self.enabled and self.health_status.is_healthy()
    
    def set_personality(self, v_ego: float, personality: int):
        """Update driving personality (0=eco, 1=normal, 2=sport)"""
        self.personality = personality
        
        # Adjust parameters based on personality
        if personality == 0:  # Eco - more conservative
            self.transition_manager.confidence_threshold = 0.8
            self.LEAD_TTC_CRITICAL = 2.0
        elif personality == 2:  # Sport - more aggressive
            self.transition_manager.confidence_threshold = 0.6
            self.LEAD_TTC_CRITICAL = 1.5
        else:  # Normal - balanced
            self.transition_manager.confidence_threshold = 0.7
            self.LEAD_TTC_CRITICAL = 1.75


# Import time for performance monitoring
try:
    import time
except ImportError:
    # Fallback for testing
    class time:
        @staticmethod
        def time():
            return 0.0
