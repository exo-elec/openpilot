#!/usr/bin/env python3
"""
OSM Hybrid A* 500m Planner for OpenPilot

Integrates existing OpenPilot navigation stack:
- mapd: OSM road geometry, speed limits, curvature (0-500m)
- gridd: Occupancy grid from PCD (0-100m real-time + historical)
- Hybrid A*: Kinematically feasible path planning

Architecture:
    mapd ──► mapData (road geometry, curves, speed limits)
    gridd ──► gridObjects (BEV occupancy grid from PCD)

    pathd ──► OsmHybridPlanner
                  │
                  ├──► Parse 500m road segments from OSM
                  ├──► Overlay occupancy grid obstacles
                  ├──► Hybrid A* path planning
                  └──► enhancedTrajectory (500m)

This replaces the naive pathd with intelligent 500m planning.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


from openpilot.selfdrive.pathd.hybrid_astar import (
    HybridAStarPlanner, PlannerConfig
)
from openpilot.common.swaglog import cloudlog

@dataclass
class RoadSegment:
    """Road segment from OSM mapData."""
    start_m: float          # Distance from vehicle
    end_m: float
    curvature_1pm: float    # 0 = straight
    speed_limit_ms: float
    road_type: str          # 'motorway', 'trunk', 'primary', etc.
    num_lanes: int = 2

    @property
    def is_curve(self) -> bool:
        return self.curvature_1pm > 0.001  # ~600m radius


@dataclass
class PlannedPath:
    """500m planned path with metadata."""
    waypoints: list[tuple[float, float, float]]  # x, y, theta
    speeds_ms: list[float]  # Target speed per waypoint
    road_segments: list[RoadSegment]

    # Source data
    osm_range_m: float = 0.0
    grid_range_m: float = 0.0

    # Planning stats
    plan_time_ms: float = 0.0
    success: bool = False

    def get_speed_at(self, distance_m: float) -> float:
        """Get target speed at given distance."""
        for i, wp in enumerate(self.waypoints):
            if wp[0] >= distance_m:
                return self.speeds_ms[i] if i < len(self.speeds_ms) else 0
        return self.speeds_ms[-1] if self.speeds_ms else 0


class MapDataParser:
    """Parse OSM mapData message into road segments."""

    def parse(self, map_data_msg) -> list[RoadSegment]:
        """
        Extract 500m road segments from mapData.

        Uses:
        - upcomingCurvatureDEPRECATED: List of (distance, curvature)
        - speedLimit: Current speed limit
        - roadType: Road classification
        """
        segments = []

        try:
            # Get curvature points
            curves = []
            if hasattr(map_data_msg, 'upcomingCurvatureDEPRECATED'):
                for pt in map_data_msg.upcomingCurvatureDEPRECATED:
                    curves.append((float(pt.x), float(pt.y)))  # (distance, curvature)

            if not curves:
                # No map data - return single straight segment
                return [RoadSegment(
                    start_m=0, end_m=500,
                    curvature_1pm=0.0,
                    speed_limit_ms=getattr(map_data_msg, 'speedLimit', 30.0),
                    road_type=getattr(map_data_msg, 'roadType', 'unknown')
                )]

            # Group curvature points into segments
            # Simplified: create segments between curvature changes
            speed_limit = getattr(map_data_msg, 'speedLimit', 30.0)
            road_type = getattr(map_data_msg, 'roadType', 'unknown')

            # Create segments from curvature data
            prev_dist = 0.0
            prev_curv = 0.0

            for dist, curv in curves:
                if dist > 500:
                    break

                # New segment when curvature changes significantly
                if abs(curv - prev_curv) > 0.001 or dist - prev_dist > 100:
                    segments.append(RoadSegment(
                        start_m=prev_dist,
                        end_m=dist,
                        curvature_1pm=prev_curv,
                        speed_limit_ms=speed_limit,
                        road_type=road_type
                    ))
                    prev_dist = dist
                    prev_curv = curv

            # Final segment to 500m
            if prev_dist < 500:
                segments.append(RoadSegment(
                    start_m=prev_dist,
                    end_m=500,
                    curvature_1pm=prev_curv,
                    speed_limit_ms=speed_limit,
                    road_type=road_type
                ))

            return segments

        except Exception as e:
            cloudlog.debug(f"Failed to parse mapData: {e}")
            return []


class OccupancyGridParser:
    """Parse gridd occupancyGrid into obstacles."""

    def parse(self, grid_msg) -> list[tuple[float, float, float]]:
        """
        Extract obstacles from gridObjects message.

        Returns list of (x, y, radius) for occupied cells.
        """
        obstacles = []

        try:
            # gridObjects has occupancy grid
            int(grid_msg.width)
            int(grid_msg.height)
            float(grid_msg.resolution)
            float(grid_msg.originX)
            float(grid_msg.originY)

            # Find occupied cells
            for obj in grid_msg.objects:
                x = float(obj.x)  # Already in vehicle frame
                y = float(obj.y)
                score = float(obj.score)

                if score > 0.5:  # Occupied
                    # Approximate radius from object size
                    radius = max(getattr(obj, 'widthM', 0.5), getattr(obj, 'heightM', 0.5))
                    obstacles.append((x, y, radius))

            return obstacles

        except Exception as e:
            cloudlog.debug(f"Failed to parse grid: {e}")
            return []


class RoadCenterlineCostmap:
    """
    Costmap that follows OSM road centerline.

    Uses road geometry from mapd + obstacles from gridd.
    """

    def __init__(
        self,
        road_segments: list[RoadSegment],
        grid_obstacles: list[tuple[float, float, float]]
    ):
        self.segments = road_segments
        self.obstacles = grid_obstacles

        # Default lane width
        self.lane_width_m = 3.5

    def is_collision_free(self, x: float, y: float, theta: float) -> bool:
        """Check if pose is within drivable corridor."""
        # Check against obstacles from gridd
        for ox, oy, orad in self.obstacles:
            dist = math.hypot(x - ox, y - oy)
            if dist < orad + 0.5:  # 0.5m safety margin
                return False

        # Check road boundaries (stay within lane)
        seg = self._get_segment_at(x)
        if seg:
            half_width = self.lane_width_m * seg.num_lanes / 2
            if abs(y) > half_width:
                return False

        return True

    def get_cost(self, x: float, y: float) -> float:
        """Get cost at position (for planning heuristics)."""
        seg = self._get_segment_at(x)
        if seg is None:
            return 1.0

        # Prefer center of lane
        lane_center_offset = abs(y)
        center_cost = lane_center_offset / (self.lane_width_m / 2)

        # Curvature cost (slower in curves)
        curve_cost = seg.curvature_1pm * 100  # Scale up

        return center_cost + curve_cost

    def _get_segment_at(self, x: float) -> RoadSegment | None:
        """Get road segment at given distance."""
        for seg in self.segments:
            if seg.start_m <= x <= seg.end_m:
                return seg
        return None


class OsmHybridPlanner:
    """
    500m Hybrid A* planner using OSM road data + gridd occupancy.

    This is the main planner for pathd, replacing naive trajectory filtering
    with intelligent 500m planning.
    """

    def __init__(self):
        # Parsers
        self.map_parser = MapDataParser()
        self.grid_parser = OccupancyGridParser()

        # Hybrid A* planner
        config = PlannerConfig(
            xy_resolution=0.5,
            step_size=2.0,
            max_iterations=10000,
            max_search_time=0.05  # 50ms budget
        )
        self.planner = HybridAStarPlanner(config)

        # Cache
        self._last_road_segments: list[RoadSegment] = []
        self._last_grid_obstacles: list[tuple[float, float, float]] = []

        cloudlog.info("OsmHybridPlanner initialized (500m OSM + grid)")

    def plan(
        self,
        map_data_msg,
        grid_msg,
        current_speed_ms: float,
        v_cruise_ms: float
    ) -> PlannedPath | None:
        """
        Plan 500m trajectory.

        Args:
            map_data_msg: mapData from mapd (OSM geometry)
            grid_msg: gridObjects from gridd (occupancy)
            current_speed_ms: Current vehicle speed
            v_cruise_ms: Target cruise speed

        Returns:
            PlannedPath or None
        """
        start_time = time.monotonic()

        try:
            # Step 1: Parse OSM road geometry
            road_segments = self.map_parser.parse(map_data_msg)
            if not road_segments:
                return None

            self._last_road_segments = road_segments

            # Step 2: Parse occupancy grid
            grid_obstacles = self.grid_parser.parse(grid_msg)
            self._last_grid_obstacles = grid_obstacles

            # Step 3: Create costmap
            costmap = RoadCenterlineCostmap(road_segments, grid_obstacles)

            # Step 4: Plan path with Hybrid A*
            # Goal is 500m ahead on road centerline
            goal = self._compute_goal(road_segments)

            waypoints = self.planner.plan(
                start=(0.0, 0.0, 0.0),
                goal=goal,
                costmap=costmap,
                timeout=0.05
            )

            if not waypoints:
                return None

            # Step 5: Compute speed profile
            speeds = self._compute_speeds(
                waypoints, road_segments, current_speed_ms, v_cruise_ms
            )

            # Success
            plan_time_ms = (time.monotonic() - start_time) * 1000

            # Determine ranges
            osm_range = max(seg.end_m for seg in road_segments) if road_segments else 0
            grid_range = max(x for x, y, r in grid_obstacles) if grid_obstacles else 0

            return PlannedPath(
                waypoints=waypoints,
                speeds_ms=speeds,
                road_segments=road_segments,
                osm_range_m=osm_range,
                grid_range_m=grid_range,
                plan_time_ms=plan_time_ms,
                success=True
            )

        except Exception as e:
            cloudlog.debug(f"Planning failed: {e}")
            return None

    def _compute_goal(
        self,
        road_segments: list[RoadSegment]
    ) -> tuple[float, float, float]:
        """Compute goal pose 500m ahead."""
        sum(seg.end_m - seg.start_m for seg in road_segments)

        # Build centerline through segments
        x, y, theta = 0.0, 0.0, 0.0

        for seg in road_segments:
            seg_length = seg.end_m - seg.start_m

            if seg.curvature_1pm > 0.001:
                # Curved segment
                radius = 1.0 / seg.curvature_1pm
                angle = seg_length / radius

                # Arc motion
                x += radius * math.sin(angle)
                y += radius * (1 - math.cos(angle))
                theta += angle
            else:
                # Straight
                x += seg_length * math.cos(theta)
                y += seg_length * math.sin(theta)

        return (x, y, theta)

    def _compute_speeds(
        self,
        waypoints: list[tuple[float, float, float]],
        road_segments: list[RoadSegment],
        current_speed_ms: float,
        v_cruise_ms: float
    ) -> list[float]:
        """Compute speed profile for waypoints."""
        speeds = []

        for wp in waypoints:
            x = wp[0]  # Distance along path

            # Find road segment at this distance
            seg = None
            for s in road_segments:
                if s.start_m <= x <= s.end_m:
                    seg = s
                    break

            if seg is None:
                speeds.append(v_cruise_ms)
                continue

            # Base speed from speed limit or cruise
            speed = min(v_cruise_ms, seg.speed_limit_ms) if seg.speed_limit_ms > 0 else v_cruise_ms

            # MTSC: Reduce for curves
            if seg.curvature_1pm > 0:
                # v = sqrt(a_lateral / curvature), a_lat ~ 2 m/s²
                max_curve_speed = math.sqrt(2.0 / seg.curvature_1pm)
                speed = min(speed, max_curve_speed)

            speeds.append(speed)

        return speeds

    def get_debug_info(self) -> dict[str, Any]:
        """Get debug information."""
        return {
            "road_segments": len(self._last_road_segments),
            "grid_obstacles": len(self._last_grid_obstacles),
            "planner_stats": self.planner.get_stats(),
        }


def main():
    """Test/demo function."""
    print("OSM + Hybrid A* Planner")
    print("=" * 50)

    OsmHybridPlanner()

    # Simulate road segments
    segments = [
        RoadSegment(0, 100, 0.0, 30.0, "primary", 2),
        RoadSegment(100, 200, 0.005, 30.0, "primary", 2),  # Curve
        RoadSegment(200, 500, 0.0, 20.0, "secondary", 1),
    ]

    print("\nTest Road:")
    for seg in segments:
        curve_info = "CURVE" if seg.is_curve else "straight"
        print(f"  {seg.start_m:>3.0f}-{seg.end_m:>3.0f}m: {curve_info:8} " +
              f"limit={seg.speed_limit_ms*3.6:.0f}km/h")

    print("\n✅ OsmHybridPlanner ready")


if __name__ == "__main__":
    main()
