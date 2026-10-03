#!/usr/bin/env python3
"""
SQSC - Surface Quality Speed Controller (Simplified)

Real-time speed control based on surfaceStatus from surfaced daemon.
Like VTSC/MTSC: consumes perception output, produces speed limits.

Previous version had internal IMU/stereo analyzers - now reads from surfaced.

Parameters:
  - EOPSQSCEnabled - Master enable
  - EOPSQSCProfile (sport/balanced/comfort)
  - EOPSQSCUseLearnedSpeed - Use driver-learned speeds from history
"""
from __future__ import annotations

import time
import math
from collections import deque

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL

from openpilot.selfdrive.controls.lib.surface_quality_db import (
    SurfaceQualityDB, SurfacePrediction
)


class PredictiveSurfaceAnalyzer:
    """
    GPS-based predictive surface analyzer.
    Like MTSC for curves - looks up historical surface quality ahead.
    """

    def __init__(self):
        self.db = SurfaceQualityDB()
        self.predictions: list[SurfacePrediction] = []
        self.rough_sections: list[dict] = []
        self.last_lookup_time = 0.0
        self.lookup_interval = 1.0

    def update(self, lat: float, lon: float, heading: float, v_ego_ms: float) -> list[SurfacePrediction]:
        """Update predictive analysis."""
        current_time = time.monotonic()

        if current_time - self.last_lookup_time < self.lookup_interval:
            return self.predictions

        self.last_lookup_time = current_time

        # Get predictions at dynamic distances based on speed
        self.predictions = self.db.predict_ahead(lat, lon, heading, v_ego_ms)

        # Also get rough sections for planning
        self.rough_sections = self.db.get_rough_sections_ahead(
            lat, lon, heading, max_distance_m=500.0, min_quality_score=0.4
        )

        return self.predictions

    def get_best_prediction(self, v_ego_ms: float) -> SurfacePrediction | None:
        """Get most relevant prediction for current speed."""
        if not self.predictions:
            return None

        # At higher speeds, care about further distances
        if v_ego_ms >= 24:  # >= 86 kph (EVP zone 3)
            target_distances = [200, 300, 150, 100, 50]
        elif v_ego_ms >= 12:  # >= 43 kph (EVP zone 2)
            target_distances = [100, 150, 50, 200, 300]
        else:
            target_distances = [50, 100, 150, 200, 300]

        for target in target_distances:
            for pred in self.predictions:
                if abs(pred.distance_m - target) < 30 and pred.confidence > 0.3:
                    return pred

        # Fall back to first with data
        for pred in self.predictions:
            if pred.confidence > 0.3:
                return pred

        return None

    def get_most_restrictive_ahead(self, max_distance_m: float = 300) -> SurfacePrediction | None:
        """Get the most restrictive (lowest speed) prediction within range."""
        if not self.predictions:
            return None

        best = None
        lowest_speed = float('inf')

        for pred in self.predictions:
            if pred.distance_m <= max_distance_m and pred.confidence > 0.3:
                speed = pred.recommended_speed_ms if pred.recommended_speed_ms > 0 else 30.0
                if speed < lowest_speed:
                    lowest_speed = speed
                    best = pred

        return best

    def record_surface(
        self, lat: float, lon: float, heading: float, quality_score: float,
        shock_count: int, texture: str, v_ego_ms: float, section_length_m: float = 0.0
    ):
        """Record surface quality to database."""
        self.db.record_surface(
            lat, lon, quality_score=quality_score, shock_count=shock_count,
            texture=texture, heading=heading, v_ego_ms=v_ego_ms,
            section_length_m=section_length_m
        )


class SQSC:
    """
    Surface Quality Speed Controller - SIMPLIFIED VERSION.

    Consumes surfaceStatus from surfaced daemon (real-time perception).
    Consumes GPS for predictive history lookup.

    Like VTSC/MTSC: perception (surfaced) + control (SQSC) separation.
    """

    PROFILES = {
        'sport': {
            'quality_threshold': 0.6,
            'speed_reduction': 0.3,
            'transition_rate': 8.0,
            'min_speed_kph': 60,
            'use_learned': False,
        },
        'balanced': {
            'quality_threshold': 0.4,
            'speed_reduction': 0.5,
            'transition_rate': 5.0,
            'min_speed_kph': 40,
            'use_learned': True,
        },
        'comfort': {
            'quality_threshold': 0.2,
            'speed_reduction': 0.7,
            'transition_rate': 3.0,
            'min_speed_kph': 20,
            'use_learned': True,
        },
    }

    def __init__(self):
        self.params = Params()

        # Cached params — refresh at most once per second
        self._last_param_update = 0.0
        self._param_interval = 1.0
        self._cached_enabled = False
        self._cached_lookahead = False
        self._cached_use_learned = False
        self._cached_shock_limit = 15.0
        self._cached_shock_duration = 3.0
        self._cached_record_min_speed_ms = 5.0
        self._cached_record_min_lead_m = 40.0
        self._cached_record_min_quality = 0.25
        self._update_cached_params()

        self.profile = self._read_profile()
        self.sensitivity = self._read_sensitivity()
        self.config = self.PROFILES[self.profile].copy()

        self.quality_score = 0.0
        self.quality_confidence = 0.0
        self.current_limit_kph = 200.0
        self.target_limit_kph = 200.0

        self.shock_active = False
        self.shock_limit_kph = self._cached_shock_limit
        self.shock_duration_s = self._cached_shock_duration
        self.shock_end_time = 0.0

        self.quality_history = deque(maxlen=10)

        # Predictive analyzer (GPS-based history)
        self.predictive_analyzer = PredictiveSurfaceAnalyzer()

        # Recording state
        self.last_record_time = 0.0
        self.record_interval = 5.0
        self.last_lat = 0.0
        self.last_lon = 0.0
        self.section_start_quality = None
        self.section_start_time = 0.0

        # Recording guard thresholds (traffic jam / curve filter)
        # Only record when there is a genuine surface reason, not congestion.
        self._record_min_speed_ms = self._cached_record_min_speed_ms
        self._record_min_lead_m = self._cached_record_min_lead_m
        self._record_min_quality = self._cached_record_min_quality
        self._record_curve_lat_acc = 1.3     # m/s² — VTSC ENTERING threshold

        # get_state() accesses these before update() may set them
        self.predictions: list = []
        self.lookahead_enabled = self._cached_lookahead
        self.use_learned_speed = self._cached_use_learned

    def _update_cached_params(self):
        """Cache params — file I/O no more than once per second."""
        now = time.monotonic()
        if now - self._last_param_update < self._param_interval:
            return
        self._last_param_update = now

        self._cached_enabled = self.params.get_bool("EOPSQSCEnabled")
        self._cached_lookahead = self.params.get_bool("EOPSQSCLookaheadEnabled")
        self._cached_use_learned = self.params.get_bool("EOPSQSCUseLearnedSpeed")
        self._cached_shock_limit = self._read_param_float("EOPShockSpeedLimit", 15.0)
        self._cached_shock_duration = self._read_param_float("EOPShockDuration", 3.0)
        self._cached_record_min_speed_ms = self._read_param_float("EOPSQSCRecordMinSpeedKph", 18.0) / 3.6
        self._cached_record_min_lead_m = self._read_param_float("EOPSQSCRecordMinLeadM", 40.0)
        self._cached_record_min_quality = self._read_param_float("EOPSQSCRecordMinQuality", 0.25)
        self._cached_profile = self._read_profile()
        self._cached_sensitivity = self._read_sensitivity()

    def _read_param_float(self, key: str, default: float) -> float:
        try:
            val = self.params.get(key)
            if val is not None:
                return float(val.decode() if isinstance(val, bytes) else val)
        except (ValueError, TypeError):
            pass
        return default

    def _read_profile(self) -> str:
        try:
            profile = self.params.get("EOPSQSCProfile")
            if profile is not None:
                profile = profile.decode() if isinstance(profile, bytes) else profile
                if profile in self.PROFILES:
                    return profile
        except Exception:
            pass
        return 'balanced'

    def _read_sensitivity(self) -> float:
        try:
            sens = self.params.get("EOPSQSCSensitivity")
            if sens is not None:
                return max(0.5, min(2.0, float(sens)))
        except Exception:
            pass
        return 1.0

    def _extract_heading(self, gps_data) -> float:
        if gps_data is None:
            return 0.0
        if hasattr(gps_data, 'bearingDeg'):
            return gps_data.bearingDeg
        if hasattr(gps_data, 'velocityNED'):
            vn, ve, _ = gps_data.velocityNED
            return math.degrees(math.atan2(ve, vn))
        return 0.0

    def _should_record(self, v_ego_ms: float, surface_quality, shock_count: int, sm: messaging.SubMaster) -> bool:
        """
        Traffic jam / curve guard — prevent polluting surface DB with congestion speeds.

        Returns True only when ALL hold:
          1. Moving at recording-worthy speed (not stopped in traffic)
          2. Not tailgating in congestion (lead car is far or absent)
          3. There is a genuine surface reason (roughness OR shock detected)
          4. Not currently in a curve section (CSLB handles curves)
        """
        # 1. Speed gate
        if v_ego_ms < self._record_min_speed_ms:
            return False

        # 2. Lead car / congestion gate
        try:
            lead = sm['radarState'].leadOne
            if lead.status and lead.dRel < self._record_min_lead_m:
                return False
        except Exception:
            pass  # radarState unavailable — no lead detected, allow recording

        # 3. Surface reason gate
        quality_score = surface_quality.score if surface_quality else 0.0
        if quality_score < self._record_min_quality and shock_count == 0:
            return False

        # 4. Curve gate — skip if VTSC would also be active (CSLB owns that)
        try:
            model_v2 = sm['modelV2']
            if len(model_v2.orientationRate.z) > 0:
                yaw_rate = abs(model_v2.orientationRate.z[0])
                lat_acc = v_ego_ms * yaw_rate
                if lat_acc > self._record_curve_lat_acc:
                    return False
        except Exception:
            pass  # modelV2 unavailable — allow recording

        return True

    def _apply_shock_response(self, current_time: float) -> float | None:
        """Handle immediate shock response from surfaceStatus."""
        if self.shock_active and current_time > self.shock_end_time:
            self.shock_active = False

        if self.shock_active:
            return self.shock_limit_kph / 3.6
        return None

    def _get_predictive_limit(self, v_ego_ms: float) -> float | None:
        """Get speed limit from predictive analysis."""
        if not self.predictions:
            return None

        best_pred = self.predictions[0] if self.predictions else None
        if best_pred and best_pred.confidence > 0.3:
            # Use learned recommended speed if available and enabled
            if self.use_learned_speed and self.config.get('use_learned', True):
                if best_pred.recommended_speed_ms > 0:
                    return best_pred.recommended_speed_ms

            # Calculate from quality score
            effective_quality = min(best_pred.quality_score * self.sensitivity, 1.0)
            if effective_quality > self.config['quality_threshold']:
                excess = (effective_quality - self.config['quality_threshold']) / \
                         (1.0 - self.config['quality_threshold'])
                reduction = excess * self.config['speed_reduction']
                base_speed_kph = max(v_ego_ms * 3.6, 80.0)
                limit_kph = max(base_speed_kph * (1.0 - reduction), self.config['min_speed_kph'])
                return limit_kph / 3.6

        return None

    def _get_realtime_limit(self, v_ego_ms: float, surface) -> float | None:
        """
        Get speed limit from surfaceStatus (real-time from surfaced).

        Args:
            v_ego_ms: Current speed
            surface: surfaceStatus message from surfaced
        """
        if not surface.hasSurfaceQuality:
            return None

        quality = surface.surfaceQuality
        effective_quality = min(quality.score * self.sensitivity, 1.0)

        if effective_quality < self.config['quality_threshold']:
            return None

        excess = (effective_quality - self.config['quality_threshold']) / \
                 (1.0 - self.config['quality_threshold'])
        reduction = excess * self.config['speed_reduction']
        base_speed_kph = max(v_ego_ms * 3.6, 80.0)
        limit_kph = max(base_speed_kph * (1.0 - reduction), self.config['min_speed_kph'])

        return limit_kph / 3.6

    def _phase_shock_detection(self, sm: messaging.SubMaster, current_time: float) -> float | None:
        """Phase 1: Immediate shock detection from surfaceStatus."""
        if sm.updated['surfaceStatus']:
            surface = sm['surfaceStatus']
            if surface.hasShocks:
                for shock in surface.shocks:
                    if shock.distanceM < 0.1:
                        self.shock_active = True
                        self.shock_end_time = current_time + self.shock_duration_s
                    elif shock.distanceM < 20.0 and shock.severity > 0.5:
                        self.shock_active = True
                        self.shock_end_time = current_time + self.shock_duration_s
        return self._apply_shock_response(current_time)

    def _phase_predictive(self, v_ego: float, sm: messaging.SubMaster) -> float | None:
        """Phase 2: GPS-based predictive surface quality lookup."""
        if not self.lookahead_enabled or not sm.updated['gpsLocation']:
            return None
        gps = sm['gpsLocation']
        self.last_lat = gps.latitude
        self.last_lon = gps.longitude
        self.predictions = self.predictive_analyzer.update(
            self.last_lat, self.last_lon, self._extract_heading(gps), v_ego
        )
        return self._get_predictive_limit(v_ego)

    def _phase_realtime(self, v_ego: float, sm: messaging.SubMaster):
        """Phase 3: Real-time surface quality from surfaceStatus.

        Returns (realtime_limit, surface_quality) tuple.
        """
        if not sm.updated['surfaceStatus']:
            return None, None
        surface = sm['surfaceStatus']
        limit = self._get_realtime_limit(v_ego, surface)
        quality = None
        if surface.hasSurfaceQuality:
            quality = surface.surfaceQuality
            self.quality_score = quality.score
            self.quality_confidence = quality.confidence
        return limit, quality

    def _phase_fusion(self, predictive_limit: float | None, realtime_limit: float | None):
        """Phase 4: Combine predictive + real-time (most restrictive wins)."""
        if predictive_limit is not None and realtime_limit is not None:
            self.target_limit_kph = min(predictive_limit, realtime_limit) * 3.6
        elif predictive_limit is not None:
            self.target_limit_kph = predictive_limit * 3.6
        elif realtime_limit is not None:
            self.target_limit_kph = realtime_limit * 3.6
        else:
            self.target_limit_kph = 200.0

        # Apply rate limiting
        dt = DT_MDL
        max_change = self.config['transition_rate'] * dt
        if self.target_limit_kph < self.current_limit_kph:
            max_change *= 1.5  # Faster slowing
            self.current_limit_kph = max(self.current_limit_kph - max_change, self.target_limit_kph)
        else:
            self.current_limit_kph = min(self.current_limit_kph + max_change, self.target_limit_kph)

    def _phase_record(self, v_ego: float, surface_quality, sm: messaging.SubMaster, current_time: float):
        """Phase 5: Record to history database (with traffic jam / curve guard)."""
        if not surface_quality or (current_time - self.last_record_time) <= self.record_interval:
            return
        if abs(self.last_lat) < 0.1 and abs(self.last_lon) < 0.1:
            return

        # Count shocks from surfaceStatus
        shock_count = 0
        if sm.updated['surfaceStatus']:
            surface = sm['surfaceStatus']
            if surface.hasShocks:
                shock_count = len(list(surface.shocks))

        if not self._should_record(v_ego, surface_quality, shock_count, sm):
            return

        # Calculate section length if continuous rough road
        section_length = 0.0
        if self.section_start_quality is not None:
            if surface_quality.score > 0.3:
                section_length = (current_time - self.section_start_time) * v_ego
            else:
                self.section_start_quality = None
        elif surface_quality.score > 0.3:
            self.section_start_quality = surface_quality.score
            self.section_start_time = current_time

        self.predictive_analyzer.record_surface(
            self.last_lat, self.last_lon,
            self._extract_heading(sm['gpsLocation']) if sm.updated['gpsLocation'] else 0.0,
            surface_quality.score, shock_count, surface_quality.texture,
            v_ego, section_length
        )
        self.last_record_time = current_time

    def update(self, v_ego: float, sm: messaging.SubMaster) -> float | None:
        """
        Main SQSC update - consumes surfaceStatus from surfaced.

        Returns target speed in m/s, or None if SQSC not limiting.
        """
        self._update_cached_params()
        if not self._cached_enabled:
            return None

        self.profile = self._cached_profile
        self.sensitivity = self._cached_sensitivity
        self.config = self.PROFILES[self.profile].copy()
        self.lookahead_enabled = self._cached_lookahead
        self.use_learned_speed = self._cached_use_learned

        self.shock_limit_kph = self._cached_shock_limit
        self.shock_duration_s = self._cached_shock_duration

        # Update recording guard thresholds from cached params
        self._record_min_speed_ms = self._cached_record_min_speed_ms
        self._record_min_lead_m = self._cached_record_min_lead_m
        self._record_min_quality = self._cached_record_min_quality

        current_time = time.monotonic()

        # Phase 1: Shock detection
        shock_limit = self._phase_shock_detection(sm, current_time)
        if shock_limit is not None:
            return shock_limit

        # Phase 2: Predictive (GPS history)
        predictive_limit = self._phase_predictive(v_ego, sm)

        # Phase 3: Real-time quality
        realtime_limit, surface_quality = self._phase_realtime(v_ego, sm)

        # Phase 4: Fusion
        self._phase_fusion(predictive_limit, realtime_limit)

        # Phase 5: Record to database
        self._phase_record(v_ego, surface_quality, sm, current_time)

        # Return limit if active
        if self.current_limit_kph < 150.0:
            return self.current_limit_kph / 3.6
        return None

    def get_state(self) -> dict:
        """Get SQSC state for debugging."""
        current_time = time.monotonic()
        db_stats = self.predictive_analyzer.db.get_stats()

        return {
            'profile': self.profile,
            'sensitivity': self.sensitivity,
            'quality_score': self.quality_score,
            'quality_confidence': self.quality_confidence,
            'current_limit_kph': self.current_limit_kph,
            'target_limit_kph': self.target_limit_kph,
            'active': self.current_limit_kph < 150.0,
            'shock_active': self.shock_active,
            'shock_time_remaining': max(0, self.shock_end_time - current_time) if self.shock_active else 0,
            'lookahead_enabled': self.lookahead_enabled,
            'use_learned_speed': self.use_learned_speed,
            'predictions': [
                {
                    'distance': p.distance_m,
                    'quality': p.quality_score,
                    'recommended_speed_kph': p.recommended_speed_ms * 3.6,
                    'feature': p.feature_type,
                    'confidence': p.confidence
                }
                for p in self.predictions
            ],
            'rough_sections_ahead': self.predictive_analyzer.rough_sections[:3],
            'history_db': db_stats
        }
