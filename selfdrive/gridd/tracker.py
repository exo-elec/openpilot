#!/usr/bin/env python3
"""
Multi-Object Tracker for GridD

Centralized tracking for all perception inputs:
- Combines detections from monod (multi-camera)
- Associates with stereo depth points
- Maintains temporal consistency
- Outputs stable track IDs for pathd

Tracking Pipeline:
  monod detections ──┐
                     ├──▶ Tracker ──▶ Tracks ──▶ pathd
  stereo depth ──────┘

Author: EnhancedOpenPilot
"""
import numpy as np
from dataclasses import dataclass, field
from collections import defaultdict
from openpilot.common.swaglog import cloudlog

@dataclass
class Detection:
    """2D/3D detection from any source."""
    # 3D position (road frame, ego-centered)
    x: float  # forward (meters)
    y: float  # lateral (meters, left positive)
    z: float  # up (meters)
    
    # 2D image coordinates (normalized 0-1)
    u: float  # center x
    v: float  # center y
    
    # Detection metadata
    class_name: str
    confidence: float
    camera_source: str  # "wide_road", "road", "tele_road", "stereo_left", "stereo_right"
    
    # Physical size (meters)
    width: float = 0.0
    height: float = 0.0
    
    # Detection quality
    sigma_x: float = 1.0  # Position uncertainty forward
    sigma_y: float = 0.5  # Position uncertainty lateral


@dataclass
class Track:
    """Temporal track of an object."""
    track_id: int
    
    # Current state (Kalman-filtered)
    x: float = 0.0  # forward
    y: float = 0.0  # lateral
    z: float = 0.0  # up
    vx: float = 0.0  # forward velocity
    vy: float = 0.0  # lateral velocity
    
    # Uncertainty (covariance diagonal)
    sigma_x: float = 1.0
    sigma_y: float = 1.0
    sigma_vx: float = 5.0
    sigma_vy: float = 2.0
    
    # Classification
    class_name: str = "unknown"
    confidence: float = 0.0
    
    # Metadata
    age: int = 0  # Frames since creation
    hits: int = 0  # Total associations
    misses: int = 0  # Consecutive misses
    
    # Source tracking
    camera_sources: set = field(default_factory=set)
    last_camera: str = ""
    
    def predict(self, dt: float = 0.05):
        """Kalman prediction step (constant velocity)."""
        # State transition: x = x + vx*dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        
        # Increase uncertainty
        self.sigma_x += self.sigma_vx * dt * dt + 0.1
        self.sigma_y += self.sigma_vy * dt * dt + 0.1
        self.sigma_vx += 0.5
        self.sigma_vy += 0.2
        
        self.age += 1
        self.misses += 1
    
    def update(self, det: Detection, dt: float = 0.05):
        """Kalman update step with new detection."""
        # Kalman gain
        kx = self.sigma_x / (self.sigma_x + det.sigma_x)
        ky = self.sigma_y / (self.sigma_y + det.sigma_y)
        
        # Update position
        self.x += kx * (det.x - self.x)
        self.y += ky * (det.y - self.y)
        self.z = det.z
        
        # Update velocity (if we have history)
        if self.hits > 0:
            self.vx = 0.8 * self.vx + 0.2 * (det.x - self.x) / dt
            self.vy = 0.8 * self.vy + 0.2 * (det.y - self.y) / dt
        
        # Reduce uncertainty
        self.sigma_x *= (1 - kx)
        self.sigma_y *= (1 - ky)
        
        # Update metadata
        self.class_name = det.class_name
        self.confidence = det.confidence
        self.hits += 1
        self.misses = 0
        self.camera_sources.add(det.camera_source)
        self.last_camera = det.camera_source


class MultiObjectTracker:
    """Hungarian algorithm-based multi-object tracker."""
    
    def __init__(
        self,
        max_age: int = 5,           # Max frames without update
        min_hits: int = 3,          # Min hits to confirm track
        max_distance: float = 5.0,  # Max association distance (meters)
        dt: float = 0.05,           # Time step (20Hz)
    ):
        self.max_age = max_age
        self.min_hits = min_hits
        self.max_distance = max_distance
        self.dt = dt
        
        self.tracks: list[Track] = []
        self.next_id = 1
        
        # Statistics
        self.frame_count = 0
        self.total_tracks = 0
    
    def _compute_distance(self, track: Track, det: Detection) -> float:
        """Compute Mahalanobis distance between track and detection."""
        dx = (track.x - det.x) / max(track.sigma_x, 0.1)
        dy = (track.y - det.y) / max(track.sigma_y, 0.1)
        
        # Add class mismatch penalty
        class_penalty = 0.0 if track.class_name == det.class_name else 10.0
        
        return np.sqrt(dx*dx + dy*dy) + class_penalty
    
    def _associate(self, tracks: list[Track], detections: list[Detection]) -> tuple[list, list, list]:
        """Hungarian association between tracks and detections.
        
        Returns:
            (matches, unmatched_tracks, unmatched_detections)
        """
        if len(tracks) == 0 or len(detections) == 0:
            return [], list(range(len(tracks))), list(range(len(detections)))
        
        # Compute cost matrix
        cost_matrix = np.zeros((len(tracks), len(detections)))
        for i, track in enumerate(tracks):
            for j, det in enumerate(detections):
                cost_matrix[i, j] = self._compute_distance(track, det)
        
        # Greedy association (simpler than full Hungarian for small matrices)
        matches = []
        unmatched_tracks = list(range(len(tracks)))
        unmatched_dets = list(range(len(detections)))
        
        while True:
            # Find minimum cost match
            min_val = self.max_distance
            min_i = min_j = -1
            
            for i in unmatched_tracks:
                for j in unmatched_dets:
                    if cost_matrix[i, j] < min_val:
                        min_val = cost_matrix[i, j]
                        min_i, min_j = i, j
            
            if min_i < 0:
                break
            
            matches.append((min_i, min_j))
            unmatched_tracks.remove(min_i)
            unmatched_dets.remove(min_j)
        
        return matches, unmatched_tracks, unmatched_dets
    
    def update(self, detections: list[Detection]) -> list[Track]:
        """Update tracker with new detections.
        
        Args:
            detections: list of Detection objects from all cameras
            
        Returns:
            list of confirmed tracks
        """
        self.frame_count += 1
        
        # Predict all tracks
        for track in self.tracks:
            track.predict(self.dt)
        
        # Associate detections with tracks
        matches, unmatched_tracks, unmatched_dets = self._associate(
            self.tracks, detections
        )
        
        # Update matched tracks
        for track_idx, det_idx in matches:
            self.tracks[track_idx].update(detections[det_idx], self.dt)
        
        # Create new tracks for unmatched detections
        for det_idx in unmatched_dets:
            det = detections[det_idx]
            new_track = Track(
                track_id=self.next_id,
                x=det.x,
                y=det.y,
                z=det.z,
                class_name=det.class_name,
                confidence=det.confidence,
                sigma_x=det.sigma_x,
                sigma_y=det.sigma_y,
            )
            new_track.camera_sources.add(det.camera_source)
            new_track.last_camera = det.camera_source
            new_track.hits = 1
            new_track.misses = 0
            
            self.tracks.append(new_track)
            self.next_id += 1
            self.total_tracks += 1
        
        # Remove dead tracks
        self.tracks = [
            t for t in self.tracks
            if t.misses < self.max_age and t.age < self.max_age * 2
        ]
        
        # Return confirmed tracks
        confirmed = [t for t in self.tracks if t.hits >= self.min_hits]
        
        return confirmed
    
    def get_active_tracks(self) -> list[Track]:
        """Get all currently active tracks."""
        return [t for t in self.tracks if t.misses < self.max_age]
    
    def reset(self):
        """Reset all tracks."""
        self.tracks = []
        self.next_id = 1
        self.frame_count = 0


def mono_detections_to_detections(mono_dets) -> list[Detection]:
    """Convert cereal monoDetections to Detection objects."""
    detections = []
    
    if mono_dets is None:
        return detections
    
    for det in mono_dets.detections:
        detections.append(Detection(
            x=det.x,
            y=det.y,
            z=det.z,
            u=det.u,
            v=det.v,
            class_name=det.className,
            confidence=det.confidence,
            camera_source=det.cameraSource,
            width=det.width,
            height=det.height,
            sigma_x=det.sigmaX,
            sigma_y=det.sigmaY,
        ))
    
    return detections
