"""
Lazy BEV (Bird's Eye View) occupancy grid with probabilistic filtering.

This module implements a probabilistic occupancy grid using Bayes filtering,
following Autoware's approach but optimized for embedded NPU deployment.

Key features:
- Probabilistic occupancy using Bayes filter (not binary)
- Temporal persistence via exponential decay of probability
- "Lazy mapping": cells only updated when sensor data arrives
- Multi-sensor fusion ready (stereo + segmentation + cameras)
- Camera geometry-aware projection for multi-camera fusion

Grid (BEV_GRID, shared by every gridObjects layer): rows = forward, cols =
lateral, y LEFT positive like every yRel (CLAUDE.md "Frame conventions", as
sunnypilot/comma). Stereo X points right, so it is flipped once, here.

Inputs (per frame):
  xyz_map  : HxWx3 float32 — stereo XYZ point cloud (X=right, Z=forward)
  obj_mask : HxW uint8    — SceneSeg foreground pixels (1=object)
  road_mask: HxW uint8    — PPLiteSeg road pixels (1=road)

Optional (for multi-camera fusion):
  camera_points : dict[str, np.ndarray] — XYZ points from each camera
  geometry      : CameraArrayGeometry — Camera calibration/geometry

Output:
  GridResult dict with fields used to populate gridObjects cereal message
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import cast
import numpy as np
from openpilot.system.hardware import HARDWARE
from openpilot.nagaspilot.daemons.gridd.camera_geometry import CameraArrayGeometry

# Grid parameters
SAFE_WIDTH_M = 2.2      # corridor half-width each side
RANGE_M = HARDWARE.get_max_reliable_depth_m()  # ExoPilot 01M: 80m
RESOLUTION_M = 0.5      # meters per grid cell
DECAY_TIME_S = 2.0      # cell half-life (seconds)

# Bayes filter parameters (from Autoware's probabilistic occupancy grid)
P_OCCUPIED_TO_OCCUPIED = 0.95   # Probability transition: occupied -> occupied
P_OCCUPIED_TO_FREE = 0.05       # Probability transition: occupied -> free
P_FREE_TO_OCCUPIED = 0.20       # Probability transition: free -> occupied (false positive)
P_FREE_TO_FREE = 0.80           # Probability transition: free -> free

# Detection probabilities
P_DETECTION_OCCUPIED = 0.85     # P(detection | occupied) - true positive rate
P_DETECTION_FREE = 0.15         # P(detection | free) - false positive rate

# Thresholds
OCCUPANCY_THRESHOLD = 0.5       # Cell considered occupied if P(occupied) > threshold
CUTIN_THRESHOLD = 0.7           # Higher confidence needed for cut-in alarm
MIN_PROBABILITY = 0.01          # Minimum probability to avoid log(0)
MAX_PROBABILITY = 0.99          # Maximum probability to avoid log(1)



@dataclass(frozen=True)
class BEVGridSpec:
    """The one BEV grid layout: gridObjects, every layer, and pathd's decode.

    Rows run forward from origin_forward, cols run LEFT from origin_left (the
    right edge). gridObjects carries it as width=cols, height=rows,
    originX=origin_left (lateral), originY=origin_forward, as its schema says.
    """
    resolution: float = RESOLUTION_M
    origin_forward: float = -20.0   # m, rear edge
    origin_left: float = -15.0      # m, right edge (y left positive)
    rows: int = 150                 # forward: -20..+55 m
    cols: int = 60                  # lateral: -15..+15 m

    @property
    def forward_max(self) -> float:
        return self.origin_forward + self.rows * self.resolution

    def cells(self, forward: np.ndarray, left: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(rows, cols, inside) for points in metres; rows/cols valid only where inside."""
        rows = np.floor((np.asarray(forward) - self.origin_forward) / self.resolution).astype(np.int32)
        cols = np.floor((np.asarray(left) - self.origin_left) / self.resolution).astype(np.int32)
        inside = (rows >= 0) & (rows < self.rows) & (cols >= 0) & (cols < self.cols)
        return rows, cols, inside

    def center(self, row, col) -> tuple[np.ndarray, np.ndarray]:
        """(forward, left) of cell centres."""
        return (self.origin_forward + (np.asarray(row) + 0.5) * self.resolution,
                self.origin_left + (np.asarray(col) + 0.5) * self.resolution)


BEV_GRID = BEVGridSpec()

# Precompute log odds for efficiency
LOG_ODDS_OCCUPIED_TO_OCCUPIED = np.log(P_OCCUPIED_TO_OCCUPIED / (1 - P_OCCUPIED_TO_OCCUPIED))
LOG_ODDS_OCCUPIED_TO_FREE = np.log(P_OCCUPIED_TO_FREE / (1 - P_OCCUPIED_TO_FREE))
LOG_ODDS_FREE_TO_OCCUPIED = np.log(P_FREE_TO_OCCUPIED / (1 - P_FREE_TO_OCCUPIED))
LOG_ODDS_FREE_TO_FREE = np.log(P_FREE_TO_FREE / (1 - P_FREE_TO_FREE))
LOG_ODDS_DETECTION_OCCUPIED = np.log(P_DETECTION_OCCUPIED / (1 - P_DETECTION_OCCUPIED))
LOG_ODDS_DETECTION_FREE = np.log(P_DETECTION_FREE / (1 - P_DETECTION_FREE))


def probability_to_log_odds(p: np.ndarray | float) -> np.ndarray | float:
    """Convert probability to log odds."""
    p = np.clip(p, MIN_PROBABILITY, MAX_PROBABILITY)
    return cast(np.ndarray | float, np.log(p / (1 - p)))


def log_odds_to_probability(l: np.ndarray | float) -> np.ndarray | float:
    """Convert log odds back to probability."""
    return cast(np.ndarray | float, 1.0 / (1.0 + np.exp(-l)))


class ProbabilisticLazyBEV:
    """
    Probabilistic BEV grid using Bayes filtering.

    Each cell stores log-odds of occupancy:
    - Positive log-odds: likely occupied
    - Negative log-odds: likely free
    - Near zero: unknown

    Bayes filter update:
    1. Prediction: Apply temporal decay (cells become more uncertain over time)
    2. Correction: Update with sensor observation using Bayes rule

    Multi-camera support:
    - Integrates points from multiple cameras using camera geometry
    - Range-aware weighting (tele_road for far, wide_road for near)
    - Camera-specific confidence based on lens characteristics
    """

    def __init__(
        self,
        spec: BEVGridSpec = BEV_GRID,
        safe_width_m: float = SAFE_WIDTH_M,
        decay_time_s: float = DECAY_TIME_S,
        geometry: CameraArrayGeometry | None = None,
    ):
        self.spec = spec
        self.range_m = min(RANGE_M, spec.forward_max)
        self.resolution_m = spec.resolution
        self.safe_width_m = safe_width_m
        self.decay_time_s = decay_time_s

        # Camera geometry for multi-camera fusion
        self.geometry = geometry

        # Grid dimensions: rows = forward, cols = lateral (left positive)
        self.grid_h = spec.rows
        self.grid_w = spec.cols

        # Persistent state: log-odds of occupancy
        # Initialize to prior (unknown): log(0.5/0.5) = 0
        self.log_odds: np.ndarray = np.zeros((self.grid_h, self.grid_w), dtype=np.float32)

        # Last update time per cell (for decay calculation)
        self._last_update: np.ndarray = np.full((self.grid_h, self.grid_w), -1e9, dtype=np.float64)
        self._last_frame_time: float = time.monotonic()

        # Prior probability (unknown state)
        self.prior_log_odds = 0.0

        # Camera confidence weights (based on lens characteristics)
        self._camera_weights = self._init_camera_weights()

    def _init_camera_weights(self) -> dict[str, float]:
        """Initialize camera confidence weights based on lens characteristics."""
        if self.geometry is None:
            return {}

        weights = {}
        for name, config in self.geometry.cameras.items():
            # Weight based on focal length and typical use case
            if config.lens_focal_mm >= 15:  # TeleRoad
                weights[name] = 0.9  # High confidence for far objects
            elif config.lens_focal_mm >= 7:  # Standard/narrow
                weights[name] = 1.0  # Baseline confidence
            elif config.lens_focal_mm >= 3:  # Wide stereo
                weights[name] = 0.8  # Good for side/detection
            else:  # Ultra-wide
                weights[name] = 0.7  # Lower confidence but wide FOV

        return weights

    def _corridor_cells(self, forward: np.ndarray, x_right: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(rows, cols, mask) of stereo points in the corridor. x_right is
        stereo X (right positive); the grid is left positive, so flip once."""
        left = -x_right
        rows, cols, inside = self.spec.cells(forward, left)
        mask = inside & (forward > 0.5) & (forward < self.range_m) & (np.abs(left) <= self.safe_width_m)
        return rows[mask], cols[mask], mask

    def update_multi_camera(
        self,
        camera_points: dict[str, np.ndarray],
        obj_masks: dict[str, np.ndarray | None] = None,
        road_mask: np.ndarray | None = None,
    ) -> dict:
        """Update BEV grid with points from multiple cameras.

        Args:
            camera_points: Dict mapping camera name to XYZ points (N×3)
            obj_masks: Optional dict of foreground masks per camera
            road_mask: Optional road segmentation mask

        Returns:
            Grid metrics dict
        """
        now = time.monotonic()
        dt = now - self._last_frame_time
        self._last_frame_time = now

        # Apply temporal decay
        if dt > 0:
            self._apply_temporal_decay(dt)

        # Process points from each camera
        all_points: list[np.ndarray] = []
        all_weights: list[float] = []

        for cam_name, points in camera_points.items():
            if points is None or len(points) == 0:
                continue

            # Get camera weight
            weight = self._camera_weights.get(cam_name, 0.5)

            # Filter to valid range
            z = points[:, 2]  # forward distance
            x = points[:, 0]  # lateral position

            valid = (
                (z > 0.5) &
                (z < self.range_m) &
                (np.abs(x) <= self.safe_width_m)
            )

            if not np.any(valid):
                continue

            valid_points = points[valid]
            all_points.append(valid_points)
            all_weights.extend([weight] * len(valid_points))

        if not all_points:
            return self._metrics()

        # Concatenate all points
        all_points_arr = np.vstack(all_points)
        all_weights_arr = np.array(all_weights)

        # Map to grid cells
        row, col, mask = self._corridor_cells(all_points_arr[:, 2], all_points_arr[:, 0])
        all_weights_arr = all_weights_arr[mask]

        # Update grid with weighted observations
        unique_cells = {}
        for r, c, w in zip(row, col, all_weights_arr, strict=False):
            key = (r, c)
            if key not in unique_cells:
                unique_cells[key] = {'count': 0, 'weight': 0.0}
            unique_cells[key]['count'] += 1
            unique_cells[key]['weight'] += w

        # Apply Bayes update
        for (r, c), data in unique_cells.items():
            # Weighted observation confidence
            obs_weight = min(data['weight'] * 0.5, 2.0)
            observation_log_odds = LOG_ODDS_DETECTION_OCCUPIED * obs_weight
            self.log_odds[r, c] += observation_log_odds
            self._last_update[r, c] = now

        # Clip to reasonable bounds
        self.log_odds = np.clip(self.log_odds, -10.0, 10.0)

        return self._metrics()

    # ------------------------------------------------------------------
    def update(
        self,
        xyz_map: np.ndarray,
        obj_mask: np.ndarray | None,
        road_mask: np.ndarray | None,
    ) -> dict:
        """
        Update the probabilistic BEV grid and return corridor metrics.

        Uses Bayes filter:
        1. Prediction step: Decay uncertainty over time
        2. Correction step: Update with sensor observation
        """
        now = time.monotonic()
        dt = now - self._last_frame_time
        self._last_frame_time = now

        # ---- 1. Prediction: Temporal decay ----
        # Cells become more uncertain (move toward prior) over time
        if dt > 0:
            self._apply_temporal_decay(dt)

        if xyz_map is None or obj_mask is None:
            return self._metrics()

        # ---- 2. Extract foreground XYZ ----
        fg = obj_mask.astype(bool)
        if not np.any(fg):
            return self._metrics()

        z = xyz_map[:, :, 2][fg]   # forward distance
        x = xyz_map[:, :, 0][fg]   # lateral position

        # ---- 3. Filter to valid range ----
        valid = (
            (z > 0.5) &
            (z < self.range_m) &
            (np.abs(x) <= self.safe_width_m)
        )
        if not np.any(valid):
            return self._metrics()

        # ---- 4. Map to grid cells ----
        row, col, _ = self._corridor_cells(z[valid], x[valid])

        # ---- 5. Correction: Bayes filter update ----
        # For each occupied cell: increase log-odds
        # Use batched updates for efficiency
        unique_cells: dict[tuple[int, int], int] = {}
        for r, c in zip(row, col, strict=False):
            key = (r, c)
            unique_cells[key] = unique_cells.get(key, 0) + 1

        # Update each unique cell with Bayes rule
        for (r, c), count in unique_cells.items():
            # Multiple observations strengthen belief
            # Log-odds update: add observation likelihood
            observation_log_odds = LOG_ODDS_DETECTION_OCCUPIED * min(count * 0.5, 2.0)
            self.log_odds[r, c] += observation_log_odds
            self._last_update[r, c] = now

        # ---- 6. Ray tracing for free space (optional, expensive) ----
        # Mark cells along ray from ego to detected point as likely free
        # Skip for performance; rely on temporal decay

        # ---- 7. Clip to reasonable bounds ----
        self.log_odds = np.clip(self.log_odds, -10.0, 10.0)

        # ---- 8. Bike cut-in detection ----
        bike_cutin = self._detect_bike_cutin(xyz_map, obj_mask, road_mask)

        return self._metrics(bike_cutin=bike_cutin)

    def _apply_temporal_decay(self, dt: float) -> None:
        """
        Apply temporal decay to log-odds.

        Over time, cells should return to prior (unknown) state.
        This handles:
        - Moving objects leaving a cell
        - Temporary occlusions clearing
        - Uncertainty increasing with time
        """
        # Decay rate: how fast we return to prior
        decay_factor = np.exp(-dt / self.decay_time_s)

        # Move log-odds toward prior
        self.log_odds = self.prior_log_odds + (self.log_odds - self.prior_log_odds) * decay_factor

    def _detect_bike_cutin(
        self,
        xyz_map: np.ndarray,
        obj_mask: np.ndarray | None,
        road_mask: np.ndarray | None,
    ) -> bool:
        """Detect bike/scooter cut-in using segmentation overlap."""
        if road_mask is None or obj_mask is None:
            return False

        overlap = obj_mask.astype(bool) & road_mask.astype(bool)
        if not np.any(overlap):
            return False

        oz = xyz_map[:, :, 2][overlap]
        ox = xyz_map[:, :, 0][overlap]
        near = (oz > 0.5) & (oz < 20.0) & (np.abs(ox) < self.safe_width_m * 1.5)

        return bool(np.any(near))

    # ------------------------------------------------------------------
    def _metrics(self, *, bike_cutin: bool = False) -> dict:
        """Compute grid metrics for output."""
        # Convert log-odds to probability
        occupancy_prob = cast(np.ndarray, log_odds_to_probability(self.log_odds))

        # Cells above threshold considered occupied
        occupied = occupancy_prob >= OCCUPANCY_THRESHOLD

        # Find closest occupied cell (drivable limit)
        drivable_limit = self.range_m
        first_ahead = max(0, int(np.ceil(-self.spec.origin_forward / self.resolution_m)))
        occupied_rows = np.where(np.any(occupied[first_ahead:], axis=1))[0] + first_ahead
        if len(occupied_rows):
            drivable_limit = float(self.spec.center(occupied_rows[0], 0)[0])

        return {
            "drivable_limit_m":    drivable_limit,
            "occupancy_detected":  len(occupied_rows) > 0,
            "bike_cutin_imminent": bike_cutin,
            "grid":                occupancy_prob,  # Return probabilities, not log-odds
            "log_odds_grid":       self.log_odds.copy(),  # For debugging
        }

    def flat_grid(self) -> list[float]:
        """Flatten to list[float] for cereal serialisation."""
        occupancy_prob = cast(np.ndarray, log_odds_to_probability(self.log_odds))
        return cast(list[float], occupancy_prob.flatten().tolist())

    def get_grid(self) -> np.ndarray:
        """Return the current occupancy probability grid."""
        return cast(np.ndarray, log_odds_to_probability(self.log_odds))

    def update_from_points(self, xyz_points: np.ndarray) -> None:
        """Update the BEV grid directly from an Nx3 point cloud.

        This is the lazy BEV entry point used by gridd: only the points that
        survived the ROI filter in _lazy_reprojection are integrated.
        """
        now = time.monotonic()
        dt = now - self._last_frame_time
        self._last_frame_time = now

        if dt > 0:
            self._apply_temporal_decay(dt)

        if xyz_points is None or len(xyz_points) == 0:
            return

        z = xyz_points[:, 2]  # forward distance
        x = xyz_points[:, 0]  # lateral position

        valid = (
            (z > 0.5) &
            (z < self.range_m) &
            (np.abs(x) <= self.safe_width_m)
        )
        if not np.any(valid):
            return

        row, col, _ = self._corridor_cells(z[valid], x[valid])

        for r, c in zip(row, col, strict=False):
            self.log_odds[r, c] += LOG_ODDS_DETECTION_OCCUPIED
            self._last_update[r, c] = now

        self.log_odds = np.clip(self.log_odds, -10.0, 10.0)

    def get_cell_probability(self, forward_m: float, left_m: float) -> float:
        """Occupancy probability at (forward, left) metres, left positive."""
        row, col, inside = self.spec.cells(forward_m, left_m)
        if inside:
            return float(log_odds_to_probability(self.log_odds[int(row), int(col)]))
        return 0.5  # Unknown outside grid


# Backward compatibility: LazyBEV is now ProbabilisticLazyBEV
LazyBEV = ProbabilisticLazyBEV
