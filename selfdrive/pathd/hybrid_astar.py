#!/usr/bin/env python3
"""
Hybrid A* Local Path Planner for OpenPilot

Integrates with surfaced BEV drivable area grid to plan kinematically
feasible paths using Hybrid A* search.

Based on:
- VisionPilot global_planner/hybrid_astar.py
- Autoware Universe freespace_planner
- "Practical Search Techniques in Path Planning for Autonomous Driving" (Dolgov et al.)
"""

from __future__ import annotations

import math
import heapq
import time
from dataclasses import dataclass, field
from typing import Any
from enum import IntEnum

import numpy as np
from openpilot.common.swaglog import cloudlog

class CellState(IntEnum):
    """BEV grid cell states (from surfaced)."""
    UNKNOWN = 0
    DRIVABLE_SMOOTH = 1
    DRIVABLE_NORMAL = 2
    DRIVABLE_ROUGH = 3
    LEARNED_ROUGH = 4
    OCCUPIED = 255


@dataclass
class PlannerConfig:
    """Configuration for Hybrid A* planner."""

    # Grid resolution (should match surfaced BEV grid)
    xy_resolution: float = 0.25  # meters per cell
    theta_resolution: float = 15.0  # degrees per heading bin

    # Vehicle parameters (EOP platform)
    wheelbase: float = 2.7  # meters (typical sedan)
    max_steer_angle: float = 35.0  # degrees
    min_turning_radius: float = 5.0  # meters
    vehicle_width: float = 1.8  # meters
    vehicle_length: float = 4.5  # meters

    # Motion primitives
    num_steer_angles: int = 7  # number of steering angles to sample
    step_size: float = 1.0  # meters per expansion step

    # Search limits
    max_iterations: int = 10000
    max_search_time: float = 0.05  # 50ms for real-time

    # Costs
    steer_change_cost: float = 0.5
    steer_cost: float = 0.1
    reverse_cost: float = 5.0
    direction_change_cost: float = 10.0
    rough_surface_cost: float = 1.0  # Penalty for rough road

    # Analytic expansion
    analytic_expansion_interval: int = 20
    analytic_expansion_max_length: float = 30.0

    # Goal tolerance
    goal_xy_tolerance: float = 0.5  # meters
    goal_theta_tolerance: float = 15.0  # degrees


@dataclass(order=True)
class Node:
    """Node in the Hybrid A* search tree."""

    f_cost: float  # Total cost (g + h) for priority queue
    x: float = field(compare=False)  # meters (forward from vehicle)
    y: float = field(compare=False)  # meters (left from vehicle)
    theta: float = field(compare=False)  # radians (0 = forward)
    direction: int = field(compare=False)  # 1 = forward, -1 = reverse
    g_cost: float = field(compare=False)  # Cost from start
    steer: float = field(compare=False, default=0.0)
    parent: Node | None = field(compare=False, default=None)

    def get_index(self, config: PlannerConfig) -> tuple[int, int, int]:
        """Get discrete grid index for this node."""
        xi = int(self.x / config.xy_resolution)
        yi = int(self.y / config.xy_resolution) + 100  # Offset for negative y
        theta_deg = math.degrees(self.theta) % 360
        ti = int(theta_deg / config.theta_resolution)
        return (xi, yi, ti)


class BEVCostmap:
    """
    Costmap wrapper for surfaced BEV drivable area grid.

    Provides collision checking and cost lookup for planner.
    """

    def __init__(self, grid_data: np.ndarray, resolution: float, origin_x: float, origin_y: float):
        """
        Args:
            grid_data: BEV grid from surfaced (CellState values)
            resolution: Grid resolution in meters
            origin_x, origin_y: Grid origin in meters
        """
        self.grid = grid_data
        self.resolution = resolution
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.height, self.width = grid_data.shape

        # Vehicle footprint in grid cells
        self.vehicle_width_cells = int(1.8 / resolution)  # 1.8m vehicle width
        self.vehicle_length_cells = int(4.5 / resolution)  # 4.5m vehicle length

    def world_to_grid(self, x: float, y: float) -> tuple[int, int]:
        """Convert world coordinates to grid indices."""
        col = int((y - self.origin_y) / self.resolution)
        row = int((x - self.origin_x) / self.resolution)
        return row, col

    def is_collision_free(self, x: float, y: float, theta: float) -> bool:
        """
        Check if vehicle pose is collision-free using rotation-aware collision detection.

        Args:
            x, y: Vehicle position in meters (vehicle frame: x=forward, y=left)
            theta: Vehicle heading in radians (0 = forward, positive = left turn)
        """
        # Vehicle dimensions in meters
        half_width = 0.9   # 1.8m / 2
        half_length = 2.25  # 4.5m / 2

        # Define vehicle corners in vehicle frame (centered at origin)
        # Order: front-left, front-right, rear-right, rear-left
        corners_local = [
            (half_length, half_width),
            (half_length, -half_width),
            (-half_length, -half_width),
            (-half_length, half_width),
        ]

        # Rotation matrix for theta
        cos_theta = math.cos(theta)
        sin_theta = math.sin(theta)

        # Transform corners to world frame
        corners_world = []
        for cx, cy in corners_local:
            # Rotate
            rx = cx * cos_theta - cy * sin_theta
            ry = cx * sin_theta + cy * cos_theta
            # Translate
            corners_world.append((x + rx, y + ry))

        # Get bounding box of rotated corners for efficient grid iteration
        xs = [c[0] for c in corners_world]
        ys = [c[1] for c in corners_world]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        # Convert to grid coordinates
        min_row, min_col = self.world_to_grid(min_x, min_y)
        max_row, max_col = self.world_to_grid(max_x, max_y)

        # Add padding for safety
        padding = 1  # cells
        min_row -= padding
        max_row += padding
        min_col -= padding
        max_col += padding

        # Check each cell in bounding box using point-in-polygon test
        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                # Quick bounds check
                if row < 0 or row >= self.height or col < 0 or col >= self.width:
                    return False

                # Convert cell center to world coordinates
                cell_x = self.origin_x + row * self.resolution
                cell_y = self.origin_y + col * self.resolution

                # Check if cell center is inside rotated vehicle footprint
                if self._point_in_rectangle(cell_x, cell_y, corners_world):
                    # Cell is inside vehicle footprint - check if drivable
                    cell = self.grid[row, col]
                    if cell == CellState.OCCUPIED or cell == CellState.UNKNOWN:
                        return False

        return True

    def _point_in_rectangle(self, px: float, py: float, corners: list[tuple[float, float]]) -> bool:
        """
        Check if point is inside a convex quadrilateral (vehicle footprint).
        Uses cross product method for convex polygon.
        """
        n = len(corners)
        for i in range(n):
            x1, y1 = corners[i]
            x2, y2 = corners[(i + 1) % n]

            # Edge vector
            ex = x2 - x1
            ey = y2 - y1

            # Vector from edge start to point
            vx = px - x1
            vy = py - y1

            # Cross product (z-component)
            cross = ex * vy - ey * vx

            # For convex polygon, all cross products should have same sign
            # (assuming consistent vertex order)
            if cross < 0:  # Point is outside this edge
                return False

        return True

    def get_cost(self, x: float, y: float) -> float:
        """Get traversal cost at position."""
        row, col = self.world_to_grid(x, y)

        if row < 0 or row >= self.height or col < 0 or col >= self.width:
            return float('inf')

        cell = self.grid[row, col]

        # Cost based on surface quality
        cost_map = {
            CellState.UNKNOWN: 10.0,
            CellState.DRIVABLE_SMOOTH: 0.0,
            CellState.DRIVABLE_NORMAL: 0.1,
            CellState.DRIVABLE_ROUGH: 1.0,
            CellState.LEARNED_ROUGH: 0.5,
            CellState.OCCUPIED: float('inf'),
        }

        return cost_map.get(cell, 1.0)


class HybridAStarPlanner:
    """
    Hybrid A* path planner for local trajectory generation.

    Plans kinematically feasible paths on surfaced BEV drivable area grid.
    """

    def __init__(self, config: PlannerConfig | None = None):
        self.config = config or PlannerConfig()
        self._precompute_motion_primitives()

        # Statistics
        self._plans_attempted = 0
        self._plans_successful = 0
        self._avg_plan_time_ms = 0.0

    def _precompute_motion_primitives(self):
        """Precompute steering angles for motion primitives."""
        max_steer_rad = math.radians(self.config.max_steer_angle)
        self.steer_angles = np.linspace(
            -max_steer_rad, max_steer_rad, self.config.num_steer_angles
        )

    def plan(
        self,
        start: tuple[float, float, float],
        goal: tuple[float, float, float],
        costmap: BEVCostmap,
        timeout: float | None = None
    ) -> list[tuple[float, float, float | None]]:
        """
        Plan a path from start to goal.

        Args:
            start: (x, y, theta) start pose in meters/radians
            goal: (x, y, theta) goal pose in meters/radians
            costmap: BEV costmap for collision checking
            timeout: Optional timeout in seconds

        Returns:
            List of (x, y, theta) waypoints, or None if no path found
        """
        timeout = timeout or self.config.max_search_time
        start_time = time.monotonic()

        self._plans_attempted += 1

        # Initialize start node
        start_node = Node(
            f_cost=0.0,
            x=start[0],
            y=start[1],
            theta=start[2],
            direction=1,
            g_cost=0.0
        )
        start_node.f_cost = self._heuristic(start_node, goal)

        # Priority queue
        open_set = [start_node]
        heapq.heapify(open_set)

        # Closed set tracks visited discrete states
        closed_set: set[tuple[int, int, int]] = set()

        # Track best cost to each grid cell
        best_cost: dict[tuple[int, int, int], float] = {}

        iteration = 0

        while open_set and iteration < self.config.max_iterations:
            # Check timeout
            if time.monotonic() - start_time > timeout:
                cloudlog.debug(f"Hybrid A* timeout after {iteration} iterations")
                return None

            iteration += 1

            # Pop best node
            current = heapq.heappop(open_set)
            current_idx = current.get_index(self.config)

            # Skip if already visited
            if current_idx in closed_set:
                continue
            closed_set.add(current_idx)

            # Check if reached goal
            if self._is_goal(current, goal):
                path = self._reconstruct_path(current)
                self._update_stats(time.monotonic() - start_time, True)
                return path

            # Try analytic expansion periodically
            if iteration % self.config.analytic_expansion_interval == 0:
                analytic_path = self._try_analytic_expansion(current, goal, costmap)
                if analytic_path is not None:
                    path = self._reconstruct_path(current) + analytic_path
                    self._update_stats(time.monotonic() - start_time, True)
                    return path

            # Expand node
            for child in self._expand(current, costmap):
                child_idx = child.get_index(self.config)

                if child_idx in closed_set:
                    continue

                # Check if better path to this cell
                if child_idx in best_cost and child.g_cost >= best_cost[child_idx]:
                    continue

                best_cost[child_idx] = child.g_cost
                child.f_cost = child.g_cost + self._heuristic(child, goal)
                heapq.heappush(open_set, child)

        self._update_stats(time.monotonic() - start_time, False)
        return None

    def _expand(self, node: Node, costmap: BEVCostmap) -> list[Node]:
        """Generate successor nodes using motion primitives."""
        successors = []

        # Only forward motion for on-road driving (no reverse)
        for direction in [1]:  # [1, -1] for reverse
            for steer in self.steer_angles:
                child = self._simulate_motion(node, steer, direction)

                if child is None:
                    continue

                # Check collision
                if not costmap.is_collision_free(child.x, child.y, child.theta):
                    continue

                # Calculate cost
                surface_cost = costmap.get_cost(child.x, child.y)
                child.g_cost = node.g_cost + self._step_cost(
                    node, child, steer, direction, surface_cost
                )
                child.parent = node

                successors.append(child)

        return successors

    def _simulate_motion(
        self, node: Node, steer: float, direction: int
    ) -> Node | None:
        """Simulate vehicle motion with bicycle model."""
        step = self.config.step_size * direction
        wheelbase = self.config.wheelbase

        if abs(steer) < 1e-6:
            # Straight motion
            x_new = node.x + step * math.cos(node.theta)
            y_new = node.y + step * math.sin(node.theta)
            theta_new = node.theta
        else:
            # Turning motion
            turning_radius = wheelbase / math.tan(abs(steer))

            if turning_radius < self.config.min_turning_radius:
                return None

            arc_angle = step / turning_radius

            if steer > 0:  # Left turn
                theta_new = node.theta + arc_angle
                x_new = node.x + turning_radius * (
                    math.sin(theta_new) - math.sin(node.theta)
                )
                y_new = node.y + turning_radius * (
                    math.cos(node.theta) - math.cos(theta_new)
                )
            else:  # Right turn
                theta_new = node.theta - arc_angle
                x_new = node.x + turning_radius * (
                    math.sin(theta_new) - math.sin(node.theta)
                )
                y_new = node.y + turning_radius * (
                    math.cos(node.theta) - math.cos(theta_new)
                )

        # Normalize theta
        theta_new = math.atan2(math.sin(theta_new), math.cos(theta_new))

        return Node(
            f_cost=0.0,
            x=x_new,
            y=y_new,
            theta=theta_new,
            direction=direction,
            g_cost=0.0,
            steer=steer
        )

    def _step_cost(
        self, parent: Node, child: Node, steer: float, direction: int, surface_cost: float
    ) -> float:
        """Calculate cost for a motion step."""
        cost = self.config.step_size

        # Steering cost
        cost += self.config.steer_cost * abs(steer)

        # Steering change cost
        if parent.steer != 0:
            cost += self.config.steer_change_cost * abs(steer - parent.steer)

        # Reverse cost
        if direction == -1:
            cost += self.config.reverse_cost * self.config.step_size

        # Direction change cost
        if direction != parent.direction:
            cost += self.config.direction_change_cost

        # Surface quality cost
        cost += surface_cost * self.config.rough_surface_cost

        return cost

    def _heuristic(self, node: Node, goal: tuple[float, float, float]) -> float:
        """Calculate heuristic cost to goal."""
        dx = goal[0] - node.x
        dy = goal[1] - node.y
        dist = math.hypot(dx, dy)

        # Heading penalty
        goal_theta = goal[2]
        heading_diff = abs(math.atan2(
            math.sin(goal_theta - node.theta),
            math.cos(goal_theta - node.theta)
        ))

        # Non-holonomic heuristic (Reeds-Shepp lower bound)
        rs_cost = max(dist, heading_diff * self.config.min_turning_radius)

        return rs_cost

    def _is_goal(self, node: Node, goal: tuple[float, float, float]) -> bool:
        """Check if node is close enough to goal."""
        dx = abs(node.x - goal[0])
        dy = abs(node.y - goal[1])

        if dx > self.config.goal_xy_tolerance or dy > self.config.goal_xy_tolerance:
            return False

        # Check heading
        dtheta = abs(math.atan2(
            math.sin(goal[2] - node.theta),
            math.cos(goal[2] - node.theta)
        ))

        return dtheta < math.radians(self.config.goal_theta_tolerance)

    def _try_analytic_expansion(
        self, node: Node, goal: tuple[float, float, float], costmap: BEVCostmap
    ) -> list[tuple[float, float, float | None]]:
        """
        Try to connect to goal using Reeds-Shepp curves (CSC type).

        Reeds-Shepp curves are the shortest path for car-like vehicles.
        This implementation uses CSC (Circle-Straight-Circle) paths which cover
        most practical cases efficiently.
        """
        dx = goal[0] - node.x
        dy = goal[1] - node.y
        dist = math.hypot(dx, dy)

        if dist > self.config.analytic_expansion_max_length:
            return None

        # Minimum turning radius
        r = self.config.min_turning_radius

        # Transform goal to node-local frame
        math.atan2(dy, dx)
        math.atan2(
            math.sin(goal[2] - node.theta),
            math.cos(goal[2] - node.theta)
        )

        # Try different path types (CSC variations)
        # Path type: Left-Straight-Left (LSL), Right-Straight-Right (RSR),
        #            Left-Straight-Right (LSR), Right-Straight-Left (RSL)
        best_path = None
        best_cost = float('inf')

        for turn1_dir in [1, -1]:  # 1 = left, -1 = right
            for turn2_dir in [1, -1]:
                path = self._compute_csc_path(
                    node, goal, r, turn1_dir, turn2_dir, costmap
                )
                if path is not None:
                    # Calculate path cost (length)
                    cost = 0.0
                    for i in range(1, len(path)):
                        cost += math.hypot(
                            path[i][0] - path[i-1][0],
                            path[i][1] - path[i-1][1]
                        )
                    if cost < best_cost:
                        best_cost = cost
                        best_path = path

        # Also try straight line (degenerate case)
        straight_path = self._compute_straight_path(node, goal, costmap)
        if straight_path is not None:
            cost = math.hypot(dx, dy)
            if cost < best_cost:
                best_path = straight_path

        return best_path

    def _compute_csc_path(
        self, start: Node, goal: tuple[float, float, float], r: float,
        turn1_dir: int, turn2_dir: int, costmap: BEVCostmap
    ) -> list[tuple[float, float, float | None]]:
        """
        Compute Circle-Straight-Circle path.

        Args:
            start: Start node
            goal: Goal pose (x, y, theta)
            r: Turning radius
            turn1_dir: Direction of first turn (1 = left, -1 = right)
            turn2_dir: Direction of second turn (1 = left, -1 = right)
            costmap: Collision checking
        """
        # Transform to start frame
        dx = goal[0] - start.x
        dy = goal[1] - start.y

        # Rotate to start heading
        cos_s = math.cos(-start.theta)
        sin_s = math.sin(-start.theta)
        x = dx * cos_s - dy * sin_s
        y = dx * sin_s + dy * cos_s
        theta = math.atan2(
            math.sin(goal[2] - start.theta),
            math.cos(goal[2] - start.theta)
        )

        # Circle centers
        # First turn center (relative to start)
        c1x = 0
        c1y = turn1_dir * r

        # Second turn center (relative to end, transformed to start frame)
        c2x = x - turn2_dir * r * math.sin(theta)
        c2y = y + turn2_dir * r * math.cos(theta)

        # Distance between circle centers
        d = math.hypot(c2x - c1x, c2y - c1y)

        # Check if path is feasible
        if d < 1e-6 or d > 4 * r:  # Circles too close or too far
            return None

        # Calculate tangent points
        # For CSC path, we connect circles with a straight line
        # The line is tangent to both circles

        # Angle between circle centers
        alpha = math.atan2(c2y - c1y, c2x - c1x)

        # Angle for tangent points
        if turn1_dir == turn2_dir:
            # LSL or RSR: external tangent
            if d < 2 * r:
                return None
            beta = math.acos(min(2 * r / d, 1.0))
            tangent_angle1 = alpha + turn1_dir * beta
            tangent_angle2 = alpha + turn2_dir * beta
        else:
            # LSR or RSL: internal tangent
            if d < 2 * r:
                return None
            beta = math.acos(min(2 * r / d, 1.0))
            tangent_angle1 = alpha - turn1_dir * beta
            tangent_angle2 = alpha + turn2_dir * beta

        # Tangent points
        t1x = c1x + r * math.cos(tangent_angle1)
        t1y = c1y + turn1_dir * r * math.sin(tangent_angle1)
        t2x = c2x + r * math.cos(tangent_angle2)
        t2y = c2y + turn2_dir * r * math.sin(tangent_angle2)

        # Calculate arc angles
        # Start angle on first circle
        start_angle1 = math.atan2(-c1y, 0)  # From center to start (0, 0 relative to center)
        end_angle1 = math.atan2(t1y - c1y, t1x - c1x)

        # Start angle on second circle
        start_angle2 = math.atan2(t2y - c2y, t2x - c2x)
        end_angle2 = math.atan2(y - c2y, x - c2x)  # From center to goal

        # Normalize angles
        def normalize_angle(a):
            while a > math.pi:
                a -= 2 * math.pi
            while a < -math.pi:
                a += 2 * math.pi
            return a

        arc1_angle = normalize_angle(end_angle1 - start_angle1)
        arc2_angle = normalize_angle(end_angle2 - start_angle2)

        # Ensure correct turning direction
        if turn1_dir == 1 and arc1_angle < 0:
            arc1_angle += 2 * math.pi
        elif turn1_dir == -1 and arc1_angle > 0:
            arc1_angle -= 2 * math.pi

        if turn2_dir == 1 and arc2_angle < 0:
            arc2_angle += 2 * math.pi
        elif turn2_dir == -1 and arc2_angle > 0:
            arc2_angle -= 2 * math.pi

        # Generate path points
        path = []
        step_size = self.config.step_size / 2  # Finer resolution for collision checking

        # First arc
        arc1_length = abs(arc1_angle) * r
        n_arc1 = max(int(arc1_length / step_size), 2)

        for i in range(n_arc1):
            t = (i + 1) / n_arc1
            angle = start_angle1 + arc1_angle * t
            px = c1x + r * math.cos(angle)
            py = c1y + r * math.sin(angle)
            ptheta = angle + turn1_dir * math.pi / 2

            # Transform back to world frame
            wx = start.x + px * math.cos(start.theta) - py * math.sin(start.theta)
            wy = start.y + px * math.sin(start.theta) + py * math.cos(start.theta)
            wtheta = start.theta + ptheta
            wtheta = math.atan2(math.sin(wtheta), math.cos(wtheta))

            if not costmap.is_collision_free(wx, wy, wtheta):
                return None
            path.append((wx, wy, wtheta))

        # Straight segment
        straight_length = math.hypot(t2x - t1x, t2y - t1y)
        n_straight = max(int(straight_length / step_size), 1)
        straight_angle = math.atan2(t2y - t1y, t2x - t1x)

        for i in range(1, n_straight + 1):
            t = i / n_straight
            px = t1x + (t2x - t1x) * t
            py = t1y + (t2y - t1y) * t

            # Transform back to world frame
            wx = start.x + px * math.cos(start.theta) - py * math.sin(start.theta)
            wy = start.y + px * math.sin(start.theta) + py * math.cos(start.theta)
            wtheta = start.theta + straight_angle
            wtheta = math.atan2(math.sin(wtheta), math.cos(wtheta))

            if not costmap.is_collision_free(wx, wy, wtheta):
                return None
            path.append((wx, wy, wtheta))

        # Second arc
        arc2_length = abs(arc2_angle) * r
        n_arc2 = max(int(arc2_length / step_size), 2)

        for i in range(1, n_arc2 + 1):
            t = i / n_arc2
            angle = start_angle2 + arc2_angle * t
            px = c2x + r * math.cos(angle)
            py = c2y + r * math.sin(angle)
            ptheta = angle + turn2_dir * math.pi / 2

            # Transform back to world frame
            wx = start.x + px * math.cos(start.theta) - py * math.sin(start.theta)
            wy = start.y + px * math.sin(start.theta) + py * math.cos(start.theta)
            wtheta = start.theta + ptheta
            wtheta = math.atan2(math.sin(wtheta), math.cos(wtheta))

            if not costmap.is_collision_free(wx, wy, wtheta):
                return None
            path.append((wx, wy, wtheta))

        return path

    def _compute_straight_path(
        self, start: Node, goal: tuple[float, float, float], costmap: BEVCostmap
    ) -> list[tuple[float, float, float | None]]:
        """Compute straight line path for degenerate cases."""
        dx = goal[0] - start.x
        dy = goal[1] - start.y
        dist = math.hypot(dx, dy)

        if dist < 1e-6:
            return []

        n_points = max(int(dist / (self.config.step_size / 2)), 3)
        path = []
        theta = math.atan2(dy, dx)

        for i in range(1, n_points + 1):
            t = i / n_points
            x = start.x + dx * t
            y = start.y + dy * t

            if not costmap.is_collision_free(x, y, theta):
                return None

            path.append((x, y, theta))

        return path

    def _reconstruct_path(
        self, goal_node: Node
    ) -> list[tuple[float, float, float]]:
        """Reconstruct path from goal to start."""
        path = []
        current: Node | None = goal_node

        while current is not None:
            path.append((current.x, current.y, current.theta))
            current = current.parent

        # Reverse to get start->goal
        path.reverse()

        return path

    def _update_stats(self, plan_time: float, success: bool):
        """Update planner statistics."""
        if success:
            self._plans_successful += 1

        # Moving average
        alpha = 0.1
        self._avg_plan_time_ms = (
            (1 - alpha) * self._avg_plan_time_ms +
            alpha * (plan_time * 1000)
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
        }
