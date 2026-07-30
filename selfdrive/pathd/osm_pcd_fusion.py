#!/usr/bin/env python3
"""
OSM + PCD Fusion for Long Horizon Planning

Fuses OpenStreetMap (road geometry) with Historical PCD (surface quality)
to create a comprehensive 500m drivable corridor for:
- MTSC (Map Turn Speed Control) - road curvature
- VTSC (Vision Turn Speed Control) - surface quality  
- Hybrid A* planning - obstacle-free path

Architecture:
    mapd ──► OSM road geometry (250-500m curves)
    surfaced ──► Historical PCD (250-500m surface quality)
    
    pathd ──► OSMPCDFusion ──► Enhanced Drivable Corridor
                    │
                    ├──► MTSC: Curvature-based speed limits
                    ├──► VTSC: Surface quality speed limits  
                    └──► Hybrid A*: Obstacle-aware path planning

Reference: VisionPilot map_prediction + Autoware behavior_velocity_planner
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any
from enum import IntEnum

import numpy as np
from openpilot.common.swaglog import cloudlog

class SurfaceQuality(IntEnum):
    """Surface quality classification."""
    UNKNOWN = 0
    SMOOTH = 1      # High speed
    NORMAL = 2      # Normal speed
    ROUGH = 3       # Reduced speed
    VERY_ROUGH = 4  # Slow speed


@dataclass
class RoadSegment:
    """Road segment with OSM + PCD fused data."""
    # Position
    start_m: float          # Start distance from vehicle
    end_m: float            # End distance from vehicle
    
    # OSM data
    curvature_1pm: float    # Curvature (1/m), 0 = straight
    road_class: str         # 'motorway', 'primary', 'secondary', etc.
    speed_limit_ms: float   # OSM speed limit (m/s)
    num_lanes: int
    
    # PCD data (from historical point clouds)
    surface_quality: SurfaceQuality
    has_potholes: bool
    has_bumps: bool
    avg_roughness: float    # meters (height variation)
    
    # Fused speed recommendation
    recommended_speed_ms: float = 0.0
    confidence: float = 1.0  # 0-1 based on data freshness
    
    def compute_recommended_speed(self, base_speed_ms: float):
        """Compute recommended speed based on curvature + surface."""
        # Start with OSM speed limit or base speed
        speed = min(base_speed_ms, self.speed_limit_ms) if self.speed_limit_ms > 0 else base_speed_ms
        
        # MTSC: Reduce for curvature (lateral accel limit ~2 m/s²)
        if self.curvature_1pm > 0:
            # v = sqrt(a_lateral / curvature)
            max_curvature_speed = math.sqrt(2.0 / self.curvature_1pm)
            speed = min(speed, max_curvature_speed)
        
        # VTSC: Reduce for surface quality
        quality_factors = {
            SurfaceQuality.SMOOTH: 1.0,
            SurfaceQuality.NORMAL: 0.9,
            SurfaceQuality.ROUGH: 0.7,
            SurfaceQuality.VERY_ROUGH: 0.5,
            SurfaceQuality.UNKNOWN: 0.8,
        }
        speed *= quality_factors.get(self.surface_quality, 0.8)
        
        # Reduce for potholes/bumps
        if self.has_potholes:
            speed *= 0.8
        if self.has_bumps:
            speed *= 0.9
        
        self.recommended_speed_ms = speed
        return speed


@dataclass
class DrivableCorridor:
    """
    Drivable corridor for 500m planning.
    
    Defines the safe drivable area combining:
    - OSM road boundaries (lane geometry)
    - PCD observed drivable surface (actual drivable area)
    """
    # Corridor centerline (from OSM or hybrid A*)
    centerline: list[tuple[float, float]]  # (x, y) in vehicle frame
    
    # Boundaries at each centerline point
    left_boundary: list[tuple[float, float]]
    right_boundary: list[tuple[float, float]]
    
    # Road segments with fused data
    segments: list[RoadSegment]
    
    # Metadata
    total_length_m: float = 0.0
    has_osm_data: bool = False
    has_pcd_data: bool = False
    
    def get_segment_at(self, distance_m: float) -> RoadSegment | None:
        """Get road segment at given distance."""
        for seg in self.segments:
            if seg.start_m <= distance_m <= seg.end_m:
                return seg
        return None
    
    def get_speed_profile(self) -> list[tuple[float, float]]:
        """Get (distance_m, speed_ms) profile for entire corridor."""
        profile = []
        for seg in self.segments:
            profile.append((seg.start_m, seg.recommended_speed_ms))
            profile.append((seg.end_m, seg.recommended_speed_ms))
        return profile


class OSMPCDFusion:
    """
    Fuses OSM map data with PCD surface data.
    
    Creates unified drivable corridor for long-horizon planning.
    """
    
    def __init__(self):
        # Data sources
        self.osm_segments: list[RoadSegment] = []
        self.pcd_points: np.ndarray | None = None
        
        # Fusion parameters
        self.pcd_influence_radius_m = 5.0  # How far PCD affects OSM segment
        self.min_pcd_points_per_segment = 10
        
        cloudlog.info("OSMPCDFusion initialized")
    
    def update_osm(self, map_data_msg) -> None:
        """Update OSM road segments from mapd."""
        self.osm_segments = []
        
        try:
            # Parse OSM road geometry from mapData message
            for segment in map_data_msg.segments:
                road_seg = RoadSegment(
                    start_m=float(segment.startDistanceM),
                    end_m=float(segment.endDistanceM),
                    curvature_1pm=float(segment.curvature1Pm),
                    road_class=str(segment.roadClass),
                    speed_limit_ms=float(segment.speedLimitMs),
                    num_lanes=int(segment.numLanes),
                    surface_quality=SurfaceQuality.UNKNOWN,
                    has_potholes=False,
                    has_bumps=False,
                    avg_roughness=0.0,
                    confidence=float(segment.confidence)
                )
                self.osm_segments.append(road_seg)
            
            cloudlog.debug(f"Updated {len(self.osm_segments)} OSM segments")
            
        except Exception as e:
            cloudlog.debug(f"Failed to update OSM data: {e}")
    
    def update_pcd(self, pcd_points: np.ndarray) -> None:
        """Update PCD point cloud from surfaced/pointcloudd."""
        self.pcd_points = pcd_points
    
    def fuse(self, base_speed_ms: float) -> DrivableCorridor | None:
        """
        Fuse OSM + PCD into drivable corridor.
        
        Args:
            base_speed_ms: Desired cruise speed
            
        Returns:
            DrivableCorridor with fused data
        """
        if not self.osm_segments:
            return None
        
        try:
            # Fuse PCD surface quality into OSM segments
            for seg in self.osm_segments:
                self._fuse_pcd_to_segment(seg)
                seg.compute_recommended_speed(base_speed_ms)
            
            # Build corridor centerline from OSM
            centerline = self._build_centerline()
            left_boundary, right_boundary = self._build_boundaries()
            
            # Total length
            total_length = max(seg.end_m for seg in self.osm_segments) if self.osm_segments else 0
            
            return DrivableCorridor(
                centerline=centerline,
                left_boundary=left_boundary,
                right_boundary=right_boundary,
                segments=self.osm_segments,
                total_length_m=total_length,
                has_osm_data=True,
                has_pcd_data=self.pcd_points is not None
            )
            
        except Exception as e:
            cloudlog.debug(f"OSM+PCD fusion failed: {e}")
            return None
    
    def _fuse_pcd_to_segment(self, seg: RoadSegment) -> None:
        """Fuse PCD data into a road segment."""
        if self.pcd_points is None or len(self.pcd_points) == 0:
            return
        
        # Find PCD points within segment range
        points_in_seg = self.pcd_points[
            (self.pcd_points[:, 0] >= seg.start_m) &
            (self.pcd_points[:, 0] <= seg.end_m)
        ]
        
        if len(points_in_seg) < self.min_pcd_points_per_segment:
            return
        
        # Analyze surface quality from point heights
        heights = points_in_seg[:, 2]  # z = height
        
        # Roughness metric: standard deviation of heights
        roughness = np.std(heights)
        seg.avg_roughness = float(roughness)
        
        # Classify surface quality
        if roughness < 0.02:
            seg.surface_quality = SurfaceQuality.SMOOTH
        elif roughness < 0.05:
            seg.surface_quality = SurfaceQuality.NORMAL
        elif roughness < 0.1:
            seg.surface_quality = SurfaceQuality.ROUGH
        else:
            seg.surface_quality = SurfaceQuality.VERY_ROUGH
        
        # Detect potholes/bumps (local height anomalies)
        height_diff = np.max(heights) - np.min(heights)
        if height_diff > 0.1:  # 10cm variation
            seg.has_bumps = True
        if np.any(heights < -0.05):  # Depressions
            seg.has_potholes = True
        
        # Increase confidence with PCD data
        seg.confidence = min(1.0, seg.confidence + 0.2)
    
    def _build_centerline(self) -> list[tuple[float, float]]:
        """Build corridor centerline from OSM segments."""
        centerline = [(0.0, 0.0)]  # Start at vehicle
        
        for seg in self.osm_segments:
            # Approximate curved road segment
            if seg.curvature_1pm > 0:
                # Curved segment
                radius = 1.0 / seg.curvature_1pm
                length = seg.end_m - seg.start_m
                angle = length / radius
                
                # Sample points along curve
                num_points = max(3, int(length / 10))
                for i in range(1, num_points + 1):
                    t = i / num_points
                    dist = seg.start_m + length * t
                    theta = angle * t
                    
                    # Arc coordinates
                    x = seg.start_m + dist - seg.start_m  # Approximate
                    y = radius * (1 - math.cos(theta))
                    
                    centerline.append((x, y))
            else:
                # Straight segment
                centerline.append((seg.end_m, 0.0))
        
        return centerline
    
    def _build_boundaries(self) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        """Build left/right boundaries from lane width."""
        left_boundary = []
        right_boundary = []
        
        for seg in self.osm_segments:
            # Assume 3.5m per lane
            lane_width = 3.5
            half_width = lane_width * seg.num_lanes / 2
            
            # Sample boundary points
            num_points = max(2, int((seg.end_m - seg.start_m) / 20))
            for i in range(num_points):
                t = i / max(1, num_points - 1)
                x = seg.start_m + (seg.end_m - seg.start_m) * t
                
                left_boundary.append((x, half_width))
                right_boundary.append((x, -half_width))
        
        return left_boundary, right_boundary


class MTSCVTSCPlanner:
    """
    Combined MTSC + VTSC speed planning.
    
    Uses OSM for curvature (MTSC) and PCD for surface quality (VTSC)
    to compute optimal speed profile for 500m corridor.
    """
    
    def __init__(self):
        self.fusion = OSMPCDFusion()
        
        # Speed planning parameters
        self.max_lateral_accel = 2.0  # m/s²
        self.comfort_decel = 2.0      # m/s²
        self.min_speed_ms = 5.0       # 18 km/h
        
        cloudlog.info("MTSCVTSCPlanner initialized")
    
    def plan_speed(
        self,
        map_data_msg,
        pcd_points: np.ndarray | None,
        current_speed_ms: float,
        target_speed_ms: float
    ) -> list[tuple[float, float]]:
        """
        Plan speed profile for 500m ahead.
        
        Returns:
            List of (distance_m, speed_ms) waypoints
        """
        # Update fusion
        self.fusion.update_osm(map_data_msg)
        if pcd_points is not None:
            self.fusion.update_pcd(pcd_points)
        
        # Fuse data
        corridor = self.fusion.fuse(target_speed_ms)
        
        if corridor is None:
            # Fallback: constant speed
            return [(0, current_speed_ms), (500, target_speed_ms)]
        
        # Get raw speed profile from segments
        profile = corridor.get_speed_profile()
        
        # Apply comfort constraints (jerk limits)
        smoothed_profile = self._smooth_profile(profile, current_speed_ms)
        
        return smoothed_profile
    
    def _smooth_profile(
        self,
        profile: list[tuple[float, float]],
        current_speed_ms: float
    ) -> list[tuple[float, float]]:
        """Apply comfort smoothing to speed profile."""
        if not profile:
            return [(0, current_speed_ms), (500, current_speed_ms)]
        
        smoothed = [(0.0, current_speed_ms)]
        
        for i, (dist, target_speed) in enumerate(profile):
            if i == 0:
                continue
            
            prev_dist, prev_speed = smoothed[-1]
            delta_dist = dist - prev_dist
            
            if delta_dist <= 0:
                continue
            
            # Compute max speed change for comfort
            # v² = u² + 2as
            speed_diff = target_speed - prev_speed
            
            if speed_diff < 0:
                # Decelerating - limit by comfort decel
                max_decel_speed = math.sqrt(max(0, prev_speed**2 - 2 * self.comfort_decel * delta_dist))
                new_speed = max(target_speed, max_decel_speed)
            else:
                # Accelerating
                new_speed = target_speed
            
            new_speed = max(self.min_speed_ms, new_speed)
            smoothed.append((dist, new_speed))
        
        return smoothed


class HybridAStarWithCorridor:
    """
    Hybrid A* planner that uses OSM+PCD corridor for constraints.
    """
    
    def __init__(self):
        from openpilot.selfdrive.pathd.hybrid_astar import HybridAStarPlanner, PlannerConfig
        
        self.planner = HybridAStarPlanner(PlannerConfig(
            xy_resolution=0.5,
            max_iterations=10000,
            max_search_time=0.05
        ))
        
        self.fusion = OSMPCDFusion()
    
    def plan(
        self,
        corridor: DrivableCorridor,
        start: tuple[float, float, float],
        goal: tuple[float, float, float]
    ) -> list[tuple[float, float, float | None]]:
        """
        Plan path constrained to drivable corridor.
        
        Uses corridor boundaries as soft constraints for Hybrid A*.
        """
        # Create costmap from corridor
        class CorridorCostmap:
            def __init__(self, corridor):
                self.corridor = corridor
            
            def is_collision_free(self, x, y, theta):
                # Check if within corridor boundaries
                seg = self.corridor.get_segment_at(x)
                if seg is None:
                    return True  # Unknown area
                
                # Approximate lane width check
                lane_half_width = 3.5 * seg.num_lanes / 2
                return abs(y) <= lane_half_width
            
            def get_cost(self, x, y):
                seg = self.corridor.get_segment_at(x)
                if seg is None:
                    return 1.0
                
                # Higher cost for deviating from center
                lane_width = 3.5 * seg.num_lanes
                deviation = abs(y) / (lane_width / 2)
                return deviation * 0.5
        
        costmap = CorridorCostmap(corridor)
        
        return self.planner.plan(start, goal, costmap)


def main():
    """Demo/test function."""
    print("OSM + PCD Fusion Module")
    print("=" * 50)
    
    # Create sample OSM segments
    segments = [
        RoadSegment(
            start_m=0, end_m=100,
            curvature_1pm=0.0,  # Straight
            road_class="primary",
            speed_limit_ms=30.0,  # 108 km/h
            num_lanes=2,
            surface_quality=SurfaceQuality.SMOOTH,
            has_potholes=False,
            has_bumps=False,
            avg_roughness=0.01
        ),
        RoadSegment(
            start_m=100, end_m=200,
            curvature_1pm=0.01,  # Curve
            road_class="primary",
            speed_limit_ms=30.0,
            num_lanes=2,
            surface_quality=SurfaceQuality.ROUGH,
            has_potholes=True,
            has_bumps=False,
            avg_roughness=0.08
        ),
        RoadSegment(
            start_m=200, end_m=500,
            curvature_1pm=0.0,  # Straight
            road_class="secondary",
            speed_limit_ms=20.0,  # 72 km/h
            num_lanes=1,
            surface_quality=SurfaceQuality.NORMAL,
            has_potholes=False,
            has_bumps=False,
            avg_roughness=0.04
        ),
    ]
    
    # Compute recommended speeds
    base_speed = 30.0  # m/s (108 km/h)
    print(f"\nBase speed: {base_speed} m/s ({base_speed*3.6:.0f} km/h)")
    print("\nSegment Analysis:")
    print(f"{'Start':>6} {'End':>6} {'Curve':>8} {'Surface':>10} {'Speed':>8} {'Limit':>8}")
    print("-" * 60)
    
    for seg in segments:
        seg.compute_recommended_speed(base_speed)
        print(f"{seg.start_m:>6.0f} {seg.end_m:>6.0f} "
              f"{seg.curvature_1pm:>8.3f} "
              f"{seg.surface_quality.name:>10} "
              f"{seg.recommended_speed_ms:>7.1f} "
              f"{seg.speed_limit_ms:>7.1f}")
    
    print("\n✅ OSM + PCD Fusion module ready")


if __name__ == "__main__":
    main()
