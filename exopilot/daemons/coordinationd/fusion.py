#!/usr/bin/env python3
"""
fusion.py - Sensor fusion engine for coordinationd.

Fuses GNSS, OSM map-matching, and SGM pointcloud matching
to produce a globally-consistent position estimate.

Uses ECEF coordinates internally for accurate fusion.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from openpilot.common.transformations.coordinates import geodetic2ecef, ecef2geodetic


@dataclass
class PositionEstimate:
    """
    Fused position estimate with uncertainty.

    Contains both ECEF and geodetic representations for convenience.
    """
    ecef: np.ndarray  # 3D ECEF coordinates (meters)
    lat: float  # Latitude (degrees)
    lon: float  # Longitude (degrees)
    alt: float  # Altitude (meters above WGS84)
    timestamp: float  # Unix timestamp
    covariance: np.ndarray = field(default_factory=lambda: np.eye(3) * 100)
    source: str = "unknown"  # Source of estimate
    confidence: float = 0.0  # Confidence (0-1)
    on_road: bool = False  # True if on known road
    road_width: float = 0.0  # Road width in meters
    distance_to_road_center: float = 0.0  # Distance to road center


@dataclass
class GNSSMeasurement:
    """GNSS measurement with accuracy information."""
    lat: float
    lon: float
    alt: float
    timestamp: float
    accuracy: float  # Horizontal accuracy in meters
    speed: float
    speed_accuracy: float
    bearing: float
    ecef: np.ndarray = field(default_factory=lambda: np.zeros(3))
    is_rtk: bool = False


@dataclass
class RoadConstraint:
    """Road constraint from OSM or SGM localization."""
    lat: float
    lon: float
    road_width: float
    confidence: float
    source: str
    timestamp: float = field(default_factory=time.monotonic)


class FusionEngine:
    """
    Sensor fusion engine for global localization.

    Fuses multiple position sources using covariance-weighted averaging:
    - GNSS measurements (with RTK support)
    - OSM map-matching constraints
    - SGM pointcloud matching constraints
    - Dead reckoning from velocity

    All fusion is performed in ECEF coordinates for accuracy.
    """

    # Configuration
    MAX_GNSS_AGE = 1.0  # Maximum age of GNSS measurement (seconds)
    MAX_LOCALIZER_AGE = 2.0  # Maximum age of localizer measurement (seconds)

    # Noise parameters (meters)
    GNSS_BASE_NOISE = 5.0  # Base GNSS noise
    GNSS_RTK_NOISE = 0.02  # RTK GNSS noise
    OSM_NOISE = 10.0  # OSM map matching noise
    SGM_NOISE = 2.0  # SGM pointcloud matching noise
    VELOCITY_INTEGRATION_NOISE = 0.1  # Dead reckoning noise per second

    def __init__(self):
        """Initialize fusion engine."""
        self.current_position: PositionEstimate | None = None

        # Statistics
        self.fusion_count = 0
        self.gnss_only_count = 0
        self.osm_constrained_count = 0
        self.sgm_constrained_count = 0

    def dead_reckoning_step(self, dt: float, velocity: tuple[float, float],
                           heading: float) -> PositionEstimate | None:
        """
        Propagate position using velocity (dead reckoning).

        Args:
            dt: Time delta in seconds
            velocity: (vx, vy) velocity in m/s
            heading: Heading in radians (NED frame)

        Returns:
            Updated PositionEstimate or None if no current position
        """
        if self.current_position is None:
            return None

        vx, vy = velocity
        speed = math.sqrt(vx * vx + vy * vy)

        # Calculate displacement in NED frame
        dn = speed * dt * math.cos(heading)  # North
        de = speed * dt * math.sin(heading)  # East

        # Convert to lat/lon changes
        lat = self.current_position.lat
        dlat = dn / 111111.0  # Approximate meters to degrees
        dlon = de / (111111.0 * math.cos(math.radians(lat))) if abs(lat) < 89.9 else 0.0

        # Update position
        new_lat = lat + dlat
        new_lon = self.current_position.lon + dlon
        new_ecef = geodetic2ecef([new_lat, new_lon, self.current_position.alt])

        # Update covariance (increase uncertainty over time)
        new_cov = self.current_position.covariance + np.eye(3) * (
            self.VELOCITY_INTEGRATION_NOISE * dt
        ) ** 2

        return PositionEstimate(
            ecef=new_ecef, lat=new_lat, lon=new_lon, alt=self.current_position.alt,
            timestamp=time.monotonic(), covariance=new_cov,
            source="dead_reckoning",
            confidence=max(0.0, self.current_position.confidence - 0.1 * dt),
            on_road=self.current_position.on_road,
            road_width=self.current_position.road_width
        )

    def fuse(self,
             current_position: PositionEstimate | None,
             gnss: GNSSMeasurement | None,
             osm_constraint: RoadConstraint | None,
             sgm_constraint: RoadConstraint | None,
             use_osm: bool = True,
             use_sgm: bool = True) -> PositionEstimate | None:
        """
        Fuse all available position sources.

        Uses covariance-weighted averaging for optimal fusion:
        - Higher confidence measurements get more weight
        - ECEF coordinates used internally for accuracy

        Args:
            current_position: Previous fused position (for dead reckoning)
            gnss: GNSS measurement
            osm_constraint: OSM map-matching constraint
            sgm_constraint: SGM pointcloud matching constraint
            use_osm: Whether to use OSM constraints
            use_sgm: Whether to use SGM constraints

        Returns:
            Fused PositionEstimate or None if no measurements
        """
        self.current_position = current_position
        now = time.monotonic()

        # Start with dead reckoning if we have a current position
        if current_position is not None:
            now - current_position.timestamp
            fused = current_position  # Keep as fallback
        else:
            fused = None

        # Collect all valid measurements
        measurements: list[np.ndarray] = []
        covariances: list[np.ndarray] = []
        sources: list[str] = []

        # Add GNSS measurement
        if gnss is not None:
            age = now - gnss.timestamp
            if age < self.MAX_GNSS_AGE and gnss.ecef.any():
                measurements.append(gnss.ecef)
                # RTK gets much lower noise
                noise = self.GNSS_RTK_NOISE if gnss.is_rtk else gnss.accuracy
                covariances.append(np.eye(3) * noise ** 2)
                sources.append('gnss')

        # Reference altitude for constraints
        ref_alt = current_position.alt if current_position else (
            gnss.alt if gnss else 0.0
        )

        # Add OSM constraint
        if use_osm and osm_constraint is not None:
            age = now - osm_constraint.timestamp
            if age < self.MAX_LOCALIZER_AGE:
                osm_ecef = geodetic2ecef([
                    osm_constraint.lat, osm_constraint.lon, ref_alt
                ])
                # Noise inversely proportional to confidence
                osm_noise = (self.OSM_NOISE / max(osm_constraint.confidence, 0.1)) ** 2
                measurements.append(osm_ecef)
                covariances.append(np.eye(3) * osm_noise)
                sources.append('osm')
                self.osm_constrained_count += 1

        # Add SGM constraint
        if use_sgm and sgm_constraint is not None:
            age = now - sgm_constraint.timestamp
            if age < self.MAX_LOCALIZER_AGE:
                sgm_ecef = geodetic2ecef([
                    sgm_constraint.lat, sgm_constraint.lon, ref_alt
                ])
                # Noise inversely proportional to confidence
                sgm_noise = (self.SGM_NOISE / max(sgm_constraint.confidence, 0.1)) ** 2
                measurements.append(sgm_ecef)
                covariances.append(np.eye(3) * sgm_noise)
                sources.append('sgm')
                self.sgm_constrained_count += 1

        # No valid measurements
        if len(measurements) == 0:
            return fused

        # Single measurement - use directly
        if len(measurements) == 1:
            fused_ecef = measurements[0]
            fused_cov = covariances[0]
            source = sources[0]
            if source == 'gnss':
                self.gnss_only_count += 1
        else:
            # Multiple measurements - covariance-weighted fusion
            inv_cov_sum = np.zeros((3, 3))
            weighted_sum = np.zeros(3)

            for meas, cov in zip(measurements, covariances, strict=False):
                try:
                    inv_cov = np.linalg.inv(cov)
                    inv_cov_sum += inv_cov
                    weighted_sum += inv_cov @ meas
                except np.linalg.LinAlgError:
                    # Singular matrix - skip this measurement
                    continue

            if np.trace(inv_cov_sum) < 1e-10:
                # No valid measurements after inversion
                return fused

            fused_cov = np.linalg.inv(inv_cov_sum)
            fused_ecef = fused_cov @ weighted_sum
            source = 'fusion'
            self.fusion_count += 1

        # Convert back to geodetic
        geodetic = ecef2geodetic(fused_ecef)

        # Calculate confidence from covariance trace
        trace_cov = np.trace(fused_cov)
        confidence = max(0.0, 1.0 - trace_cov / 100.0)

        # Determine road status
        on_road = osm_constraint is not None or sgm_constraint is not None
        road_width = 0.0
        if sgm_constraint is not None:
            road_width = sgm_constraint.road_width
        elif osm_constraint is not None:
            road_width = osm_constraint.road_width

        return PositionEstimate(
            ecef=fused_ecef,
            lat=geodetic[0],
            lon=geodetic[1],
            alt=geodetic[2],
            timestamp=now,
            covariance=fused_cov,
            source=source,
            confidence=confidence,
            on_road=on_road,
            road_width=road_width
        )

    def get_stats(self) -> dict:
        """Get fusion engine statistics."""
        total = self.fusion_count + self.gnss_only_count
        return {
            'fusions': self.fusion_count,
            'gnss_only': self.gnss_only_count,
            'osm_constrained': self.osm_constrained_count,
            'sgm_constrained': self.sgm_constrained_count,
            'fusion_rate': self.fusion_count / max(total, 1)
        }
