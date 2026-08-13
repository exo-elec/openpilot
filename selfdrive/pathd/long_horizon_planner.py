#!/usr/bin/env python3
"""
Long Horizon Planner (500m) - EOP PathD Extension

Extends planning horizon from 80-250m (cameras) to 500m using:
1. Real-time: Stereo + TeleRoad (0-250m)
2. Historical PCD: 250-500m (same road, previous drives)

Architecture:
    surfaced → drivableArea (BEV grid 0-100m)
    monod → monoDetections (tele_road 50-250m)
    pointcloudd → pointcloudProcessed + saved PCD (250-500m)

    pathd → LongHorizonPlanner → enhancedTrajectory (500m)

Reference: Matches OSM planning horizon (EOPMTSCEnabled: 250-500m)
"""

from __future__ import annotations

import time
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from openpilot.selfdrive.pathd.hybrid_astar import (
    HybridAStarPlanner, PlannerConfig, BEVCostmap
)
from openpilot.selfdrive.surfaced.pcd_matcher import HistoricalPCDMatcher
from openpilot.common.swaglog import cloudlog

# Range tiers for 500m planning
NEAR_RANGE_M = 100.0   # 0-100m: surfaced BEV grid (high res)
MID_RANGE_M = 250.0    # 100-250m: tele_road + stereo (med res)
FAR_RANGE_M = 500.0    # 250-500m: historical PCD (low res)


@dataclass
class RangeTier:
    """Perception tier for different range bands."""
    name: str
    range_m: tuple[float, float]
    resolution_m: float
    source: str
    confidence: float


# Define 500m range tiers
RANGE_TIERS = [
    RangeTier("near", (0.0, NEAR_RANGE_M), 0.25, "surfaced BEV", 1.0),
    RangeTier("mid", (NEAR_RANGE_M, MID_RANGE_M), 0.5, "tele_road + stereo", 0.8),
    RangeTier("far", (MID_RANGE_M, FAR_RANGE_M), 2.0, "historical PCD", 0.6),
]


@dataclass
class LongHorizonPath:
    """500m planned trajectory with metadata."""
    waypoints: list[tuple[float, float, float]]  # x, y, theta
    velocities: list[float]  # m/s per waypoint
    confidences: list[float]  # 0-1 confidence per segment
    ranges: list[tuple[float, float]]  # (start_m, end_m) for each tier

    # Metadata
    plan_time_ms: float = 0.0
    has_pcd_data: bool = False
    pcd_fitness_score: float = 0.0

    @property
    def total_length_m(self) -> float:
        """Total path length in meters."""
        if len(self.waypoints) < 2:
            return 0.0
        length = 0.0
        for i in range(1, len(self.waypoints)):
            dx = self.waypoints[i][0] - self.waypoints[i-1][0]
            dy = self.waypoints[i][1] - self.waypoints[i-1][1]
            length += math.hypot(dx, dy)
        return length

    @property
    def effective_range_m(self) -> float:
        """Range with acceptable confidence."""
        for i, conf in enumerate(self.confidences):
            if conf < 0.5:
                # Find distance at this index
                if i < len(self.waypoints):
                    return self.waypoints[i][0]
        return self.total_length_m


class MultiTierCostmap:
    """
    Multi-resolution costmap for 500m planning.

    Combines:
    - Near (0-100m): 0.25m resolution from surfaced BEV
    - Mid (100-250m): 0.5m resolution from tele_road detections
    - Far (250-500m): 2.0m resolution from historical PCD
    """

    def __init__(self):
        self.tiers = RANGE_TIERS
        self.near_costmap: BEVCostmap | None = None
        self.mid_obstacles: list[tuple[float, float, float]] = []  # x, y, radius
        self.far_obstacles: list[tuple[float, float, float]] = []

    def update_near(self, drivable_area_msg) -> None:
        """Update near-field from surfaced BEV grid."""
        try:
            import numpy as np
            width = int(drivable_area_msg.width)
            height = int(drivable_area_msg.height)
            resolution = float(drivable_area_msg.resolution)

            data = np.frombuffer(drivable_area_msg.data, dtype=np.uint8)
            data = data.reshape((height, width))

            self.near_costmap = BEVCostmap(
                grid_data=data,
                resolution=resolution,
                origin_x=float(drivable_area_msg.originX),
                origin_y=float(drivable_area_msg.originY)
            )
        except Exception as e:
            cloudlog.debug(f"Failed to update near costmap: {e}")

    def update_mid(self, mono_detections) -> None:
        """Update mid-field from tele_road/monod detections."""
        self.mid_obstacles = []

        for det in mono_detections:
            # Filter for tele_road camera and valid range
            if det.cameraSource != 'tele_road':
                continue
            if det.distanceM < NEAR_RANGE_M or det.distanceM > MID_RANGE_M:
                continue

            # Add obstacle (inflate by object size + safety margin)
            radius = max(det.widthM, det.heightM) / 2 + 1.0  # 1m safety
            self.mid_obstacles.append((
                det.distanceM,  # x (forward)
                det.yRel,       # y (lateral)
                radius
            ))

    def update_far(self, historical_points: np.ndarray | None) -> None:
        """Update far-field from historical PCD."""
        self.far_obstacles = []

        if historical_points is None or len(historical_points) == 0:
            return

        # Simple clustering for PCD obstacles
        # In production, use proper clustering (DBSCAN, etc.)
        points = historical_points[historical_points[:, 0] > MID_RANGE_M]

        if len(points) == 0:
            return

        # Grid-based downsampling for obstacles
        grid_size = 5.0  # 5m grid
        occupied_grids = set()

        for pt in points:
            gx = int(pt[0] / grid_size)
            gy = int(pt[1] / grid_size)
            occupied_grids.add((gx, gy))

        # Convert to obstacle circles
        for gx, gy in occupied_grids:
            x = gx * grid_size + grid_size / 2
            y = gy * grid_size
            if MID_RANGE_M <= x <= FAR_RANGE_M:
                self.far_obstacles.append((x, y, grid_size))

    def is_collision_free(self, x: float, y: float, theta: float) -> bool:
        """Check if pose is collision-free across all tiers."""
        # Near field: use BEV costmap
        if x <= NEAR_RANGE_M:
            if self.near_costmap is not None:
                return self.near_costmap.is_collision_free(x, y, theta)
            return True

        # Mid field: check tele_road obstacles
        if x <= MID_RANGE_M:
            for ox, oy, orad in self.mid_obstacles:
                dist = math.hypot(x - ox, y - oy)
                if dist < orad:
                    return False
            return True

        # Far field: check PCD obstacles
        for ox, oy, orad in self.far_obstacles:
            dist = math.hypot(x - ox, y - oy)
            if dist < orad:
                return False

        return True

    def get_cost(self, x: float, y: float) -> float:
        """Get traversal cost at position."""
        # Near field: detailed cost from BEV
        if x <= NEAR_RANGE_M and self.near_costmap is not None:
            return self.near_costmap.get_cost(x, y)

        # Mid/Far: simpler cost based on confidence
        if x <= MID_RANGE_M:
            return 0.2  # Medium confidence

        return 0.5  # Lower confidence for far range


class LongHorizonPlanner:
    """
    500m trajectory planner for ExoPilot.

    Combines real-time perception with historical PCD for extended range.
    Target: Match OSM planning horizon (250-500m).
    """

    def __init__(self):
        # Hybrid A* planner for 500m
        config = PlannerConfig(
            xy_resolution=0.5,  # 0.5m for long range
            step_size=2.0,      # 2m steps for speed
            max_iterations=20000,
            max_search_time=0.05,  # 50ms budget
        )
        self.planner = HybridAStarPlanner(config)

        # PCD matcher for historical data
        self.pcd_matcher = HistoricalPCDMatcher()

        # Multi-tier costmap
        self.costmap = MultiTierCostmap()

        # Statistics
        self._plans_attempted = 0
        self._plans_successful = 0
        self._avg_plan_time_ms = 0.0

        cloudlog.info("LongHorizonPlanner initialized (500m target)")

    def plan(
        self,
        drivable_area_msg,
        mono_detections,
        lat: float,
        lon: float,
        heading: float,
        v_ego: float
    ) -> LongHorizonPath | None:
        """
        Plan 500m trajectory.

        Args:
            drivable_area_msg: surfaced BEV grid (0-100m)
            mono_detections: monod detections (includes tele_road)
            lat, lon: Current GPS position
            heading: Vehicle heading
            v_ego: Current speed

        Returns:
            LongHorizonPath or None if planning failed
        """
        start_time = time.monotonic()
        self._plans_attempted += 1

        try:
            # Step 1: Build multi-tier costmap
            self.costmap.update_near(drivable_area_msg)
            self.costmap.update_mid(mono_detections)

            # Step 2: Load and match historical PCD
            historical_points = self._load_historical_pcd(lat, lon, heading)
            self.costmap.update_far(historical_points)

            has_pcd = historical_points is not None and len(historical_points) > 0

            # Step 3: Plan in segments (adaptive resolution)
            waypoints = []
            velocities = []
            confidences = []
            ranges = []

            # Segment 1: 0-100m (detailed, from BEV)
            seg1 = self._plan_segment(
                start=(0.0, 0.0, 0.0),
                goal=(min(NEAR_RANGE_M, 80.0), 0.0, 0.0),  # 80m or to end
                tier=RANGE_TIERS[0]
            )
            if seg1:
                waypoints.extend(seg1)
                velocities.extend([v_ego] * len(seg1))
                confidences.extend([1.0] * len(seg1))
                ranges.append((0.0, NEAR_RANGE_M))

            # Segment 2: 100-250m (tele_road enhanced)
            if waypoints:
                seg2_start = waypoints[-1]
            else:
                seg2_start = (0.0, 0.0, 0.0)

            seg2 = self._plan_segment(
                start=seg2_start,
                goal=(MID_RANGE_M, 0.0, 0.0),
                tier=RANGE_TIERS[1]
            )
            if seg2:
                waypoints.extend(seg2[1:])  # Skip first (duplicate)
                velocities.extend([max(v_ego * 0.9, 20.0)] * (len(seg2) - 1))
                confidences.extend([0.8] * (len(seg2) - 1))
                ranges.append((NEAR_RANGE_M, MID_RANGE_M))

            # Segment 3: 250-500m (PCD based)
            if has_pcd and waypoints:
                seg3_start = waypoints[-1]
                seg3 = self._plan_segment(
                    start=seg3_start,
                    goal=(FAR_RANGE_M, 0.0, 0.0),
                    tier=RANGE_TIERS[2]
                )
                if seg3:
                    waypoints.extend(seg3[1:])
                    velocities.extend([max(v_ego * 0.8, 15.0)] * (len(seg3) - 1))
                    confidences.extend([0.6] * (len(seg3) - 1))
                    ranges.append((MID_RANGE_M, FAR_RANGE_M))

            if not waypoints:
                return None

            # Success
            plan_time_ms = (time.monotonic() - start_time) * 1000
            self._update_stats(plan_time_ms, True)

            return LongHorizonPath(
                waypoints=waypoints,
                velocities=velocities,
                confidences=confidences,
                ranges=ranges,
                plan_time_ms=plan_time_ms,
                has_pcd_data=has_pcd,
                pcd_fitness_score=self.pcd_matcher._avg_fitness if has_pcd else 0.0
            )

        except Exception as e:
            cloudlog.debug(f"Long horizon planning failed: {e}")
            self._update_stats((time.monotonic() - start_time) * 1000, False)
            return None

    def _load_historical_pcd(
        self,
        lat: float,
        lon: float,
        heading: float
    ) -> np.ndarray | None:
        """Load historical PCD for 250-500m range."""
        try:
            # Load PCD at position 375m ahead (midpoint of far range)
            # In production, sample multiple positions along route

            # Approximate 375m ahead position
            heading_rad = math.radians(heading)
            lat_ahead = lat + (375.0 / 111320.0) * math.cos(heading_rad)
            lon_ahead = lon + (375.0 / (111320.0 * math.cos(math.radians(lat)))) * math.sin(heading_rad)

            result = self.pcd_matcher.load_for_position(lat_ahead, lon_ahead, heading)

            if result is not None:
                points, _ = result
                return points

            return None

        except Exception as e:
            cloudlog.debug(f"Failed to load PCD: {e}")
            return None

    def _plan_segment(
        self,
        start: tuple[float, float, float],
        goal: tuple[float, float, float],
        tier: RangeTier
    ) -> list[tuple[float, float, float | None]]:
        """Plan a single segment with Hybrid A*."""
        # Create a simple costmap wrapper for this tier
        class TierCostmapWrapper:
            def __init__(self, multi_tier, tier_name):
                self.mt = multi_tier
                self.name = tier_name

            def is_collision_free(self, x, y, theta):
                return self.mt.is_collision_free(x, y, theta)

            def get_cost(self, x, y):
                return self.mt.get_cost(x, y)

        wrapper = TierCostmapWrapper(self.costmap, tier.name)

        return self.planner.plan(
            start=start,
            goal=goal,
            costmap=wrapper,
            timeout=0.02  # 20ms per segment
        )

    def _update_stats(self, plan_time_ms: float, success: bool):
        """Update planner statistics."""
        if success:
            self._plans_successful += 1

        alpha = 0.1
        self._avg_plan_time_ms = (
            (1 - alpha) * self._avg_plan_time_ms + alpha * plan_time_ms
        )

    def get_stats(self) -> dict[str, Any]:
        """Get planner statistics."""
        success_rate = (
            self._plans_successful / self._plans_attempted * 100
            if self._plans_attempted > 0 else 0
        )
        return {
            "plans_attempted": self._plans_attempted,
            "plans_successful": self._plans_successful,
            "success_rate": f"{success_rate:.1f}%",
            "avg_plan_time_ms": f"{self._avg_plan_time_ms:.1f}",
            "target_range_m": FAR_RANGE_M,
            "tiers": [t.name for t in RANGE_TIERS],
        }
