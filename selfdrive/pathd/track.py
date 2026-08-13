"""
track.py — multi-object Kalman tracker (used internally by pathd).

Input:  gridObjects (BEV grid) — extract candidate object clusters
Output: list of TrackedCluster objects (stable IDs, velocity estimates)

Uses proper Kalman filtering for smooth state estimation, following
Autoware's multi-object tracking approach but simplified for embedded deployment.
"""
from __future__ import annotations

import time
import numpy as np
from dataclasses import dataclass, field


@dataclass
class TrackedCluster:
    track_id: int
    # BEV centroid (meters: x=lateral, z=forward)
    x: float
    z: float
    vx: float = 0.0  # lateral velocity m/s
    vz: float = 0.0  # forward velocity m/s
    prob: float = 1.0
    age_frames: int = 0
    missed_frames: int = 0
    last_seen: float = field(default_factory=time.monotonic)

    # Kalman filter state for debugging
    state_covariance: np.ndarray = field(default_factory=lambda: np.eye(4))

    @property
    def dRel(self) -> float:
        return self.z

    @property
    def yRel(self) -> float:
        return self.x

    @property
    def vRel(self) -> float:
        return self.vz  # relative forward velocity


class KalmanTrack:
    """
    Kalman filter for single obstacle track.

    State vector: [x, z, vx, vz]^T
    - x: lateral position (m)
    - z: forward position (m)
    - vx: lateral velocity (m/s)
    - vz: forward velocity (m/s)

    Constant velocity model with process noise.
    """

    def __init__(self, track_id: int, x: float, z: float, prob: float = 1.0):
        self.track_id = track_id
        self.prob = prob
        self.age_frames = 0
        self.missed_frames = 0
        self.last_seen = time.monotonic()

        # State vector: [x, z, vx, vz]
        self.x = np.array([x, z, 0.0, 0.0], dtype=np.float32)

        # State covariance matrix
        self.P = np.eye(4, dtype=np.float32)
        # Initial position uncertainty
        self.P[0, 0] = 1.0  # x uncertainty
        self.P[1, 1] = 1.0  # z uncertainty
        # Initial velocity uncertainty (high, unknown at start)
        self.P[2, 2] = 10.0  # vx uncertainty
        self.P[3, 3] = 10.0  # vz uncertainty

        # Process noise covariance (Q)
        # Tuned for vehicle-like motion
        self.Q = np.diag([0.1, 0.1, 1.0, 1.0]).astype(np.float32)

        # Measurement noise covariance (R)
        # Based on stereo depth accuracy (~5% at 10m)
        self.R = np.diag([0.5, 0.5]).astype(np.float32)

        # Measurement matrix (H) - we only measure position
        self.H = np.array([[1, 0, 0, 0],
                           [0, 1, 0, 0]], dtype=np.float32)

    def predict(self, dt: float) -> None:
        """
        Prediction step: project state forward in time.

        Constant velocity model:
        x_{k+1} = x_k + vx_k * dt
        z_{k+1} = z_k + vz_k * dt
        vx_{k+1} = vx_k
        vz_{k+1} = vz_k
        """
        # State transition matrix (F)
        F = np.array([[1, 0, dt, 0],
                      [0, 1, 0, dt],
                      [0, 0, 1, 0],
                      [0, 0, 0, 1]], dtype=np.float32)

        # Predict state: x = F @ x
        self.x = F @ self.x

        # Predict covariance: P = F @ P @ F^T + Q
        self.P = F @ self.P @ F.T + self.Q

    def update(self, measurement: np.ndarray) -> None:
        """
        Correction step: fuse prediction with measurement.

        measurement: [x_meas, z_meas]
        """
        # Innovation: y = z - H @ x
        y = measurement - self.H @ self.x

        # Innovation covariance: S = H @ P @ H^T + R
        S = self.H @ self.P @ self.H.T + self.R

        # Kalman gain: K = P @ H^T @ S^{-1}
        try:
            K = self.P @ self.H.T @ np.linalg.inv(S)
        except np.linalg.LinAlgError:
            # If S is singular, skip update
            return

        # Update state: x = x + K @ y
        self.x = self.x + K @ y

        # Update covariance: P = (I - K @ H) @ P
        I = np.eye(4, dtype=np.float32)
        self.P = (I - K @ self.H) @ self.P

        # Ensure covariance stays positive semi-definite
        self.P = 0.5 * (self.P + self.P.T)

    def get_state(self) -> TrackedCluster:
        """Convert Kalman state to TrackedCluster output."""
        return TrackedCluster(
            track_id=self.track_id,
            x=float(self.x[0]),
            z=float(self.x[1]),
            vx=float(self.x[2]),
            vz=float(self.x[3]),
            prob=self.prob,
            age_frames=self.age_frames,
            missed_frames=self.missed_frames,
            last_seen=self.last_seen,
            state_covariance=self.P.copy(),
        )

    def innovation_distance(self, measurement: np.ndarray) -> float:
        """
        Compute Mahalanobis distance between prediction and measurement.
        Used for data association (gating).
        """
        y = measurement - self.H @ self.x
        S = self.H @ self.P @ self.H.T + self.R
        try:
            dist = float(np.sqrt(y.T @ np.linalg.inv(S) @ y))
        except np.linalg.LinAlgError:
            dist = float('inf')
        return dist


def _extract_clusters(
    grid: np.ndarray,
    resolution_m: float,
    half_w: int,
    threshold: float = 0.5,
    min_cells: int = 2,
) -> list[tuple[float, float, float]]:
    """
    Find connected components of occupied cells in the BEV grid.
    Returns list of (x_m, z_m, prob) cluster centroids.
    """
    import cv2
    binary = (grid >= threshold).astype(np.uint8)
    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    clusters = []
    for i in range(1, n_labels):  # skip background (label 0)
        area = stats[i, cv2.CC_STAT_AREA]
        if area < min_cells:
            continue
        cy_px, cx_px = centroids[i]  # NOTE: centroids are (x_col, y_row)
        z_m = float(cy_px) * resolution_m        # row -> forward distance
        x_m = (float(cx_px) - half_w) * resolution_m  # col -> lateral
        prob = float(np.mean(grid[labels == i]))
        clusters.append((x_m, z_m, prob))
    return clusters


class ObjectTracker:
    """
    Multi-object tracker using Kalman filtering.

    Associates BEV grid clusters across frames using nearest-neighbor
    matching with Mahalanobis distance gating.

    Each track maintains a Kalman filter for smooth state estimation.
    """
    MATCH_DIST_M = 4.0      # max Mahalanobis distance for association
    MAX_MISSED   = 5        # drop track after this many missed frames
    MIN_CONFIRM  = 2        # frames before track is considered confirmed
    MAX_TRACKS   = 20       # maximum number of tracks (performance)

    def __init__(self) -> None:
        self._tracks: dict[int, KalmanTrack] = {}
        self._next_id = 0

    def update(
        self,
        grid: np.ndarray,
        resolution_m: float,
        half_w: int,
        dt: float,
    ) -> list[TrackedCluster]:
        """
        Update tracker with new BEV grid. Returns confirmed active tracks.

        Steps:
        1. Predict all existing tracks forward in time
        2. Extract new detections from grid
        3. Associate detections to tracks using Mahalanobis distance
        4. Update matched tracks with Kalman filter
        5. Spawn new tracks for unmatched detections
        6. Remove stale tracks
        """
        # ---- 1. Predict all tracks ----
        for track in self._tracks.values():
            track.predict(dt)

        # ---- 2. Extract detections from grid ----
        detections = _extract_clusters(grid, resolution_m, half_w)

        # ---- 3. Association: Hungarian algorithm or greedy nearest-neighbor ----
        # Use greedy for simplicity (good enough for small N)
        matched_tracks: set[int] = set()
        matched_dets: set[int] = set()

        # Build cost matrix (Mahalanobis distance)
        if detections and self._tracks:
            track_ids = list(self._tracks.keys())
            n_tracks = len(track_ids)
            n_dets = len(detections)

            cost_matrix = np.full((n_tracks, n_dets), float('inf'))
            for i, tid in enumerate(track_ids):
                track = self._tracks[tid]
                for j, (dx, dz, _) in enumerate(detections):
                    measurement = np.array([dx, dz], dtype=np.float32)
                    dist = track.innovation_distance(measurement)
                    cost_matrix[i, j] = dist

            # Greedy assignment (simple and fast)
            while True:
                # Find minimum cost assignment
                min_idx = np.unravel_index(np.argmin(cost_matrix), cost_matrix.shape)
                i, j = min_idx

                if cost_matrix[i, j] > self.MATCH_DIST_M:
                    break  # No more valid matches

                # Assign
                tid = track_ids[i]
                track = self._tracks[tid]
                dx, dz, dp = detections[j]

                measurement = np.array([dx, dz], dtype=np.float32)
                track.update(measurement)
                track.prob = dp
                track.age_frames += 1
                track.missed_frames = 0
                track.last_seen = time.monotonic()

                matched_tracks.add(tid)
                matched_dets.add(j)

                # Mark as used
                cost_matrix[i, :] = float('inf')
                cost_matrix[:, j] = float('inf')

        # ---- 4. Age unmatched tracks ----
        for tid in list(self._tracks.keys()):
            if tid not in matched_tracks:
                self._tracks[tid].missed_frames += 1
                if self._tracks[tid].missed_frames > self.MAX_MISSED:
                    del self._tracks[tid]

        # ---- 5. Spawn new tracks for unmatched detections ----
        for j, (dx, dz, dp) in enumerate(detections):
            if j not in matched_dets and len(self._tracks) < self.MAX_TRACKS:
                track = KalmanTrack(self._next_id, dx, dz, dp)
                track.age_frames = 1
                self._tracks[self._next_id] = track
                self._next_id += 1

        # ---- 6. Return confirmed tracks ----
        confirmed = []
        for track in self._tracks.values():
            if track.age_frames >= self.MIN_CONFIRM:
                confirmed.append(track.get_state())

        return confirmed

    def get_track_count(self) -> int:
        """Return total number of active tracks."""
        return len(self._tracks)

    def reset(self) -> None:
        """Clear all tracks."""
        self._tracks.clear()
        self._next_id = 0
