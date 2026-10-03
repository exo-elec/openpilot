#!/usr/bin/env python3
"""
GridD — Lazy BEV Perception Daemon (Vision Layer - CRITICAL)

Consumes 2D stereo outputs and produces BEV occupancy grid for driving.
Performs lazy 3D reprojection - only reprojects pixels needed for BEV.

Inputs (2D from stereod):
  - stereoDepth: 2D disparity map + confidence (NOT 3D points!)
  - stereoDetections: 2D YOLO detections
  - monoDetections (from monod): Multi-camera YOLO (optional)
  - modelV2 (from modeld): drive_vision leads
  - drivableArea (from surfaced): Surface quality enhancement (optional)
  - drivableBev (from segd): each camera's drivable-area segmentation from the
    camera-tier card (Hailo-8 / DX-M1M), on our BEV grid; fused in
    drivable_fusion.py. gridd no longer touches the card or the road frame.

Processing:
  - Lazy reprojection: 2D disparity → 3D points (BEV ROI only)
  - Probabilistic Bayes filter: Temporal occupancy grid
  - Drivable fusion: per-camera card maps from segd (log-odds, decays to unknown)
  - Multi-sensor fusion: stereo + monod + modeld

Publishes:
  - gridObjects: BEV occupancy grid at 20Hz
  - stereoGround: Road boundaries for pathd
  - stereoObjects: Fused detections
  - gridStatus: Health monitoring (fault → selfdrived)

Architecture (SoC Platform - No PCIe):
  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
  │  stereod    │────►│    gridd    │────►│   pathd     │
  │  (2D disp)  │     │ (lazy BEV)  │     │  (policy)   │
  └─────────────┘     └──────┬──────┘     └─────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   surfaced            monoDetections        modelV2
   (optional)          (optional)

Hardware:
  - CPU (A76): Lazy reprojection, Bayes filter, fusion
  - GPU (Mali): Optional OpenCL assist for reprojection
  - Camera-tier card (Hailo-8 / DX-M1M): segd's, not gridd's; gridd only fuses its maps.
    gridd runs no NPU model of its own.

Fault Policy (degrade, no fallback):
  - No camera map from segd (no card, or stale): gridStatus.segmentationDegraded
    = true, everything else carries on. Nothing substitutes for the card, and
    it is not a fault (a fault disables openpilot).
  - gridd's own processing raising 3 frames in a row: gridStatus.fault = true.
  - CPU overload: Missed frames acceptable (Bayes filter smooths)

See: docs/eop/daemons/GRIDD.md
"""
from __future__ import annotations

import logging
import math
import os
import time
from typing import Any, cast
import numpy as np

import cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.params import Params
from openpilot.common.core_config import set_daemon_affinity

from openpilot.selfdrive.gridd.drivable_fusion import DrivableFusion
from openpilot.selfdrive.gridd.lazy_bev import BEV_GRID, LazyBEV
from openpilot.selfdrive.gridd.fusion_costmap import (
    COST_OBSTACLE, COST_ROAD, COST_UNCERTAIN, COST_UNKNOWN, FusionCostmap)
from openpilot.system.hardware import HARDWARE
from openpilot.system.hardware.camera_geometry import CameraGeometry
from openpilot.system.hardware.registry import PlatformRegistry
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware.hw import Paths
from openpilot.selfdrive.controls.radar_corner_geometry import (
    corner_local_to_vehicle_frame, encode_corner_track_id, load_corner_poses)
from openpilot.selfdrive.controls.lib.radar4d_points import points_to_obstacles

RATE = 20  # Hz
OCCUPIED_THRESHOLD = 0.7   # stereo occupancy -> obstacle cost; pathd's own grid collision threshold
SURFACE_MAX_AGE = 10       # frames surfaced's drivableArea stays usable (0.5 s)


# Detector class names -> CameraObject.ObstacleType enumerants. capnp rejects
# any other name ("car", "lead", ...) with an exception, which used to take
# gridd down on its first detection.
_OBSTACLE_TYPES = {
    'car': 'vehicle', 'truck': 'vehicle', 'bus': 'vehicle', 'lead': 'vehicle', 'vehicle': 'vehicle',
    'motorcycle': 'motorcycle', 'bicycle': 'motorcycle',
    'person': 'person',
    'traffic light': 'trafficLight',
}


def obstacle_type(value) -> int | str:
    """A value capnp accepts for CameraObject.obstacleType."""
    if isinstance(value, int):
        return value
    return _OBSTACLE_TYPES.get(str(value).lower(), 'unknown')


def lane_cache_from_model(model_v2) -> dict | None:
    """modelV2 lane lines and road edges in the fused-object frame, or None
    when the near lanes are not confident enough to use.

    modelV2 is openpilot's device frame, y positive to the RIGHT -- upstream
    ldw.py tests the left line against a negative offset and radard negates
    lead y. stereoObjects' yRel is positive LEFT. So every y is negated here,
    once, at ingestion (radard's pattern); _ego_lane_bounds() and
    _classify_lane_zone() then compare like with like. Storing the model's
    values unconverted inverted the ego lane: a car dead ahead classified as
    adjLeft and shoulder objects were never recognised as shoulder.
    """
    ll = model_v2.laneLines
    lp = model_v2.laneLineProbs
    re = model_v2.roadEdges
    if len(ll) < 4 or len(lp) < 4 or lp[1] <= 0.3 or lp[2] <= 0.3:
        return None

    def left_positive(line):
        return [-float(v) for v in line.y]

    def y0(ys):
        return ys[0] if ys else 0.0

    left, right = left_positive(ll[1]), left_positive(ll[2])
    far_left, far_right = left_positive(ll[0]), left_positive(ll[3])
    # Ordering sanity check: the far-left line lies further left than the left one
    far_left_valid = lp[0] > 0.2 and y0(far_left) > y0(left)
    far_right_valid = lp[3] > 0.2 and y0(far_right) < y0(right)
    return {
        'x':            list(ll[1].x),
        'left_y':       left,
        'right_y':      right,
        'far_left_y':   far_left if far_left_valid else None,
        'far_right_y':  far_right if far_right_valid else None,
        'left_edge_y':  left_positive(re[0]) if len(re) > 0 else None,
        'right_edge_y': left_positive(re[1]) if len(re) > 1 else None,
        'valid':        True,
    }


def fill_grid_objects(g, ts: int, occupancy: np.ndarray, cost: np.ndarray | None,
                      drivable: np.ndarray | None = None) -> None:
    """gridObjects in the BEV_GRID layout: width = cols (lateral, left
    positive), height = rows (forward), originX lateral / originY forward as
    the schema says. Layers: "occupancy" (probability, uint8), "cost"
    (uint16 per OccupancyGridEncoding.cost, FusionCostmap classes) and, when
    segd's maps are fresh, "drivable" (fused probability of road, uint8; the
    off-road filter in radar_zones reads it)."""
    g.timestamp = ts
    g.resolution = BEV_GRID.resolution
    g.width = BEV_GRID.cols
    g.height = BEV_GRID.rows
    g.originX = BEV_GRID.origin_left
    g.originY = BEV_GRID.origin_forward
    layers = g.init('layers', 1 + (cost is not None) + (drivable is not None))
    layers[0].name = "occupancy"
    layers[0].encoding = 'probability'
    layers[0].data = (np.clip(occupancy, 0.0, 1.0) * 255).astype(np.uint8).tobytes()
    layers[0].scale = 1.0 / 255.0
    if cost is not None:
        layers[1].name = "cost"
        layers[1].encoding = 'cost'
        layers[1].data = cost.astype('<u2').tobytes()
        layers[1].scale = 1.0
    if drivable is not None:
        layer = layers[len(layers) - 1]
        layer.name = "drivable"
        layer.encoding = 'probability'
        layer.data = (np.clip(drivable, 0.0, 1.0) * 255).astype(np.uint8).tobytes()
        layer.scale = 1.0 / 255.0


class GridD:
    """
    Lazy BEV perception daemon - fuses 2D stereo outputs.

    Performs on-demand 3D reprojection from 2D disparity.
    Critical path: fault here reduces ADAS to lane keeping only.
    """

    # Calibration path for Q matrix (3D reprojection).
    # Canonical factory-intrinsics filename; the HAL loader also falls back to
    # the legacy stereo_calibration.npz during migration.
    CALIBRATION_PATH = os.path.join(Paths.eop_data_root(), "calibration", "stereo_intrinsics.npz")

    def __init__(self) -> None:
        # Set CPU affinity to A76 cores (big cores) - safety critical
        set_daemon_affinity("gridd")

        self.params = Params()

        # Load camera geometry from HAL (hardware abstraction)
        hardware = cast(Any, PlatformRegistry.create())
        self.geometry: CameraGeometry = hardware.get_camera_geometry()
        cloudlog.info(f"GridD loaded HAL geometry: {self.geometry.variant}")

        # Load stereo calibration (Q matrix for reprojection)
        self.Q = self._load_calibration()
        if self.Q is None:
            cloudlog.warning("No stereo calibration, using default")
            self.Q = self._default_calibration()

        # BLE Radar2D uses the pose jointly calibrated by the ESP32 and host.
        # already levels BLE tracks with roll/pitch; this host step applies
        # the remaining per-corner yaw and XY translation. Approximate
        # installation priors are calibration/display aids, never ADAS input.
        self._corner_local_to_vehicle_frame = corner_local_to_vehicle_frame
        self._r2d_corner_pose = {}
        self._corner_pose_reload_t = -1.0
        self._corner_pose_warn_t = -30.0
        self._refresh_corner_poses(force=True)

        # Pub/Sub
        self.pm = messaging.PubMaster(['gridObjects', 'stereoGround', 'stereoObjects', 'gridStatus'])
        self.sm = messaging.SubMaster(
            ['monoDetections', 'monoStatus',
             'stereoDepth', 'stereoStatus',
             'modelV2', 'drivableArea',
             'drivableBev',   # segd: each camera's card segmentation on our BEV grid
             'livePose',      # signed forward speed, to move the fused drivable layer
             'radar3d',   # long-range UART radar — 15-200m, all tracked points
             'radar2d',   # corner/blind-spot zone sensors — 0-10m presence
             'radar4d'],  # corner WiFi point cloud (02M add-on), vehicle frame
            poll=cast(str | None, ['stereoDepth'])
        )

        self.rk = Ratekeeper(RATE, print_delay_threshold=None)

        # Perception modules. The camera-tier card belongs to segd alone; its
        # per-camera maps arrive as drivableBev and are fused here. Without
        # them gridd degrades (gridStatus.segmentationDegraded) -- not a fault.
        self.drivable = DrivableFusion()
        self.bev = LazyBEV()
        self.costmap_gen = FusionCostmap()

        # Live reference to current costmap object for radar fusion methods
        self._active_costmap: FusionCostmap | None = None
        self._surface: tuple[np.ndarray, np.ndarray] | None = None
        self._surface_frame = -SURFACE_MAX_AGE - 1

        # Lane line cache — populated each loop from modelV2; used by _ego_lane_bounds() and _classify_lane_zone()
        self._lane_cache: dict = {
            'x': None, 'left_y': None, 'right_y': None,
            'far_left_y': None, 'far_right_y': None,
            'left_edge_y': None, 'right_edge_y': None,
            'valid': False,
        }

        # Fault tracking; segmentation_degraded is not a fault
        self.frame_id = 0
        self.consecutive_failures = 0
        self.fault = False
        self.fault_reason = ""
        self.segmentation_degraded = False
        self.enabled = True

        cloudlog.info("GridD initialized (geometry=%s, lazy_bev=enabled)", self.geometry.variant)

    def _load_calibration(self) -> np.ndarray | None:
        """Load stereo Q matrix for 3D reprojection (factory intrinsics from HAL)."""
        cal = HARDWARE.load_stereo_intrinsics()  # canonical path + legacy migration fallback
        if cal is not None:
            Q = cal.Q
        else:
            if not os.path.exists(self.CALIBRATION_PATH):
                return None
            try:
                Q = np.load(self.CALIBRATION_PATH)['Q']
            except Exception as e:
                cloudlog.warning(f"Failed to load calibration: {e}")
                return None
        cloudlog.info(f"Loaded calibration, baseline={-1.0/Q[3,2]:.3f}m")
        return cast(np.ndarray, Q)

    def _default_calibration(self) -> np.ndarray:
        """Default Q matrix for ExoPilot."""
        f_px = 700.0
        cx, cy = 320.0, 240.0
        baseline_m = 0.08  # 80mm default

        Q = np.array([
            [1, 0, 0, -cx],
            [0, 1, 0, -cy],
            [0, 0, 0, f_px],
            [0, 0, -1.0 / baseline_m, 0],
        ], dtype=np.float64)
        cloudlog.info(f"Using default calibration, baseline={baseline_m*1000:.0f}mm")
        return Q

    def _forward_speed(self) -> float:
        """Signed forward speed (m/s) from livePose, 0 without it."""
        if not self.sm.valid.get('livePose', False):
            return 0.0
        velocity = self.sm['livePose'].velocityDevice
        return float(velocity.x) if velocity.valid else 0.0

    def _fuse_drivable_bev(self) -> None:
        """Fuse segd's newest drivableBev camera map, if one arrived and is
        on our grid (same resolution and origin as BEV_GRID)."""
        if not self.sm.updated['drivableBev']:
            return
        msg = self.sm['drivableBev']
        if not (abs(msg.resolution - BEV_GRID.resolution) < 1e-6 and msg.width == BEV_GRID.cols
                and msg.height == BEV_GRID.rows and abs(msg.originX - BEV_GRID.origin_left) < 1e-6
                and abs(msg.originY - BEV_GRID.origin_forward) < 1e-6):
            cloudlog.warning("GridD: drivableBev is not on gridd's BEV grid, ignored")
            return
        now = time.monotonic()
        for entry in msg.cameras:
            cells = np.frombuffer(entry.data, dtype=np.uint8)
            if cells.size == BEV_GRID.rows * BEV_GRID.cols:
                self.drivable.update(entry.camera, cells.reshape(BEV_GRID.rows, BEV_GRID.cols), now)

    def _lazy_reprojection(self, stereo_depth) -> np.ndarray | None:
        """
        Lazy 3D reprojection from 2D disparity.

        Only reprojects pixels needed for BEV (ROI), not full image.
        Much faster than dense 3D reconstruction in pointcloudd.
        """
        if stereo_depth is None or not stereo_depth.disparityMap:
            return None

        if self.Q is None:
            return None

        try:
            # Decode 2D disparity
            h, w = stereo_depth.height, stereo_depth.width
            disparity = np.frombuffer(stereo_depth.disparityMap, dtype=np.float32).reshape((h, w))

            # Decode confidence if available
            confidence = None
            if stereo_depth.confidenceMap:
                confidence = np.frombuffer(stereo_depth.confidenceMap, dtype=np.float32).reshape((h, w))

            # Lazy: filter to valid disparities first
            valid_mask = disparity > 0
            if confidence is not None:
                valid_mask &= confidence > 0.3

            # Subsample for efficiency (gridd doesn't need full resolution)
            # Keep every 4th pixel - sufficient for BEV grid
            step = 4
            valid_mask[::step, ::step] &= valid_mask[::step, ::step]  # Maintain stride pattern
            valid_mask[1::step, :] = False
            valid_mask[:, 1::step] = False

            if not np.any(valid_mask):
                return None

            # Get valid pixel coordinates
            v_coords, u_coords = np.where(valid_mask)
            disparities = disparity[valid_mask]

            # Reprojection using Q matrix
            # Q = [[1, 0, 0, -cx],
            #      [0, 1, 0, -cy],
            #      [0, 0, 0,  f],
            #      [0, 0, -1/b, 0]]

            cx = -self.Q[0, 3]
            cy = -self.Q[1, 3]
            f = self.Q[2, 2]
            baseline = -1.0 / self.Q[3, 2]

            # Vectorized reprojection
            # Z = f * baseline / disparity
            # X = (u - cx) * Z / f
            # Y = (v - cy) * Z / f

            Z = f * baseline / disparities
            X = (u_coords - cx) * Z / f  # Right (lateral)
            Y = (v_coords - cy) * Z / f  # Down (vertical)

            # Stack: [right, down, forward]
            xyz = np.column_stack([X, Y, Z]).astype(np.float32)

            # Filter to BEV range (lazy - only keep points in useful range)
            # Forward: 0.5m to 80m, Lateral: +/- 15m
            in_range = (
                (xyz[:, 2] > 0.5) & (xyz[:, 2] < 80.0) &  # forward
                (np.abs(xyz[:, 0]) < 15.0)  # lateral
            )

            if not np.any(in_range):
                return None

            return xyz[in_range]

        except Exception as e:
            cloudlog.debug(f"Lazy reprojection failed: {e}")
            return None

    def _fuse_mono_detections(
        self,
        mono_dets,
        xyz_points: np.ndarray | None,
    ) -> list[dict]:
        """Fuse monoDetections with stereo depth points."""
        fused_objects: list[dict[str, Any]] = []

        if mono_dets is None or not mono_dets.detections:
            return fused_objects

        for det in mono_dets.detections:
            x = det.x  # forward (m)
            y = det.y  # lateral (m), +left -- monod's lateral_m, already the fused-object frame
            z = det.z  # up (m)

            # Validate with stereo depth if available (not for an overhead
            # signal head: the nearest stereo point by range is not on it)
            if xyz_points is not None and len(xyz_points) > 0 and det.className != 'traffic light':
                dists = np.abs(xyz_points[:, 2] - x)
                closest_idx = np.argmin(dists)

                if dists[closest_idx] < 5.0:
                    stereo_x = xyz_points[closest_idx, 2]
                    if stereo_x < 30.0:
                        x = 0.7 * stereo_x + 0.3 * x

            fused_objects.append({
                'dRel': float(x),
                'yRel': float(y),
                'zRel': float(z),
                'obstacleType': det.className.lower(),
                'confidence': det.confidence,
                'trackId': det.trackId,
                'source': det.cameraSource,
                'width': float(getattr(det, 'width', 0.0)),
                'height': float(getattr(det, 'height', 0.0)),
                'trafficLightState': int(getattr(det, 'trafficLightState', 0)),
                'trafficLightConfidence': float(getattr(det, 'trafficLightConfidence', 0.0)),
            })

        return fused_objects

    def _fuse_modeld_detections(
        self,
        model_v2,
        xyz_points: np.ndarray | None,
    ) -> list[dict]:
        """Fuse modeld (drive_vision) detections with stereo depth."""
        fused_objects: list[dict[str, Any]] = []

        if model_v2 is None:
            return fused_objects

        # Extract leads from modelV2 (drive_vision output)
        if hasattr(model_v2, 'leads'):
            for lead in model_v2.leads:
                if not lead.status:
                    continue

                x = lead.x[0]  # forward (m)
                y = lead.y[0]  # lateral (m), calibrated frame: right positive
                v = lead.v[0]  # velocity (m/s)
                prob = lead.prob

                # Validate with stereo depth
                confidence = prob
                if xyz_points is not None and len(xyz_points) > 0:
                    dists = np.sqrt(
                        (xyz_points[:, 2] - x)**2 +
                        (xyz_points[:, 0] - y)**2  # stereo X and model y both point right
                    )
                    close_mask = dists < 3.0

                    if np.any(close_mask):
                        stereo_x = np.median(xyz_points[close_mask, 2])
                        if abs(stereo_x - x) < 5.0:
                            x = 0.6 * stereo_x + 0.4 * x
                            confidence = min(prob * 1.2, 1.0)
                        else:
                            confidence = prob * 0.7

                fused_objects.append({
                    'dRel': float(x),
                    'yRel': float(-y),
                    'vRel': float(v),
                    'obstacleType': 'lead',
                    'confidence': float(confidence),
                    'trackId': 0,
                    'source': 'drive_vision',
                    'width': 1.8,
                    'height': 1.5,
                })

        return fused_objects

    _R2D_SNR_REF_DB      = 20.0   # SNR reference: car at 10m ≈ 20 dB
    _R2D_CONFIDENCE_BOOST = 0.15  # max boost from SNR + track existence

    # radar2d zone positions in vehicle frame (dRel=forward, yRel=left, m)
    _R2D_ZONE_POS = {
        0: ( 2.5,  3.0),   # left-front
        1: (-4.0,  3.0),   # left-rear
        2: ( 2.5, -3.0),   # right-front
        3: (-4.0, -3.0),   # right-rear
    }
    _R2D_ZONE_RADIUS   = 2.0   # costmap obstacle radius (m)
    _R2D_PROB          = 0.92  # hardware detection — high confidence

    def _refresh_corner_poses(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._corner_pose_reload_t < 1.0:
            return
        self._corner_pose_reload_t = now
        poses = load_corner_poses(require_confirmed=True)
        if poses is None:
            self._r2d_corner_pose = {}
            if now - self._corner_pose_warn_t >= 30.0:
                cloudlog.warning(
                    "GridD: BLE Radar2D waiting for a confirmed corner pose"
                )
                self._corner_pose_warn_t = now
            return
        self._r2d_corner_pose = poses

    # radar3d fusion constants (long-range UART radar — raw RadarPoint list)
    _R3D_MIN_DREL       = 0.0    # built-in 77 GHz radar owns the full forward range
    _R3D_ASSOC_M        = 3.0    # Cartesian match radius to existing stereo object (m)
    _R3D_PROB           = 0.80   # radar track confidence
    _EGO_LANE_FALLBACK_M = 1.8   # half-lane-width fallback when modelV2 lane data unavailable

    # Lane zone integer constants — must match CameraObject.LaneZone enum in log.capnp
    _LANE_ZONE_UNKNOWN        = 0
    _LANE_ZONE_EGO            = 1
    _LANE_ZONE_ADJ_LEFT       = 2
    _LANE_ZONE_ADJ_RIGHT      = 3
    _LANE_ZONE_FAR_LEFT       = 4
    _LANE_ZONE_FAR_RIGHT      = 5
    _LANE_ZONE_SHOULDER_LEFT  = 6
    _LANE_ZONE_SHOULDER_RIGHT = 7

    def _ego_lane_bounds(self, dRel: float) -> tuple[float, float]:
        """Return (right_y, left_y) defining the ego lane edges at this forward distance.

        Interpolates modelV2 laneLines[1] (left boundary) and laneLines[2] (right boundary).
        Falls back to ±1.8m when lane confidence is low or modelV2 is absent.
        An object with right_y ≤ yRel ≤ left_y is in the ego lane.
        """
        c = self._lane_cache
        if c['valid'] and c['x'] and dRel <= c['x'][-1]:
            left_y  = float(np.interp(dRel, c['x'], c['left_y']))
            right_y = float(np.interp(dRel, c['x'], c['right_y']))
            return right_y, left_y
        return -self._EGO_LANE_FALLBACK_M, self._EGO_LANE_FALLBACK_M

    def _classify_lane_zone(self, dRel: float, yRel: float) -> int:
        """8-class lane zone classification using all 4 modelV2 laneLines + road edges.

        Zones (positive yRel = left in ExoPilot convention):
          shoulderLeft | farLeft | adjLeft | EGO | adjRight | farRight | shoulderRight

        Returns _LANE_ZONE_UNKNOWN when model horizon is exceeded or dRel <= 0.
        """
        c = self._lane_cache
        if not c['valid'] or not c['x'] or dRel <= 0 or dRel > c['x'][-1]:
            return self._LANE_ZONE_UNKNOWN

        x = c['x']
        left_y  = float(np.interp(dRel, x, c['left_y']))
        right_y = float(np.interp(dRel, x, c['right_y']))

        far_left_y   = float(np.interp(dRel, x, c['far_left_y']))   if c['far_left_y']   is not None else None
        far_right_y  = float(np.interp(dRel, x, c['far_right_y']))  if c['far_right_y']  is not None else None
        left_edge_y  = float(np.interp(dRel, x, c['left_edge_y']))  if c['left_edge_y']  is not None else None
        right_edge_y = float(np.interp(dRel, x, c['right_edge_y'])) if c['right_edge_y'] is not None else None

        if right_y <= yRel <= left_y:
            return self._LANE_ZONE_EGO

        if yRel > left_y:  # left side
            if left_edge_y is not None and yRel >= left_edge_y:
                return self._LANE_ZONE_SHOULDER_LEFT
            if far_left_y is not None and yRel >= far_left_y:
                return self._LANE_ZONE_FAR_LEFT
            return self._LANE_ZONE_ADJ_LEFT

        # yRel < right_y → right side
        if right_edge_y is not None and yRel <= right_edge_y:
            return self._LANE_ZONE_SHOULDER_RIGHT
        if far_right_y is not None and yRel <= far_right_y:
            return self._LANE_ZONE_FAR_RIGHT
        return self._LANE_ZONE_ADJ_RIGHT

    def _fuse_radar3d(self, objects: list, radar3d) -> list:
        """Add long-range UART radar tracks to stereoObjects.

        Radar3d is a FORWARD-FACING front-bumper radar (dRel > 0 only).
        It cannot see cars approaching from behind — that coverage comes from
        radar2d (corner sensors) and native carState.leftBlindspot/rightBlindspot.

        Use case here: forward adjacent-lane objects (merging traffic, cut-in),
        full forward range where the corner BLE radars are advisory only.
        Ego-lane objects are skipped using lane-relative bounds from modelV2 laneLines
        so the gate adapts to curves and S-bends — ACC owns same-lane via radarState.
        """
        for pt in radar3d.points:
            if not pt.measured:              # skip pure tracker extrapolations
                continue
            if pt.dRel <= 0:                 # forward-only radar — skip any stale negative dRel
                continue
            if pt.dRel < self._R3D_MIN_DREL:
                continue
            right_y, left_y = self._ego_lane_bounds(pt.dRel)
            if right_y <= pt.yRel <= left_y:  # ego-lane → ACC owns this
                continue

            # Try to associate with an existing stereo object
            best_idx, best_dist = None, float('inf')
            for i, obj in enumerate(objects):
                d = math.hypot(obj['dRel'] - pt.dRel, obj['yRel'] - pt.yRel)
                if d < best_dist:
                    best_dist, best_idx = d, i

            if best_idx is not None and best_dist < self._R3D_ASSOC_M:
                # Annotate: radar velocity is authoritative for far objects
                if objects[best_idx].get('vRel', 0.0) == 0.0:
                    objects[best_idx]['vRel'] = float(pt.vRel)
                objects[best_idx]['confidence'] = max(
                    objects[best_idx].get('confidence', 0.5), self._R3D_PROB)
            else:
                objects.append({
                    'dRel': float(pt.dRel), 'yRel': float(pt.yRel),
                    'vRel': float(pt.vRel),
                    'confidence': self._R3D_PROB, 'prob': self._R3D_PROB,
                    'trackId': int(pt.trackId), 'obstacleType': 0,
                })
        return objects

    def _fuse_radar4d(self, radar4d) -> None:
        """Stamp the ESP32 corner nodes' WiFi point cloud into the costmap.

        Points are already vehicle-frame polar (selfdrive/controls/radar4d.py
        applied the corner poses). Raw detections, not tracks, so they only
        mark occupancy; they never become stereoObjects entries (the BLE
        Radar2D tracks from the same nodes already do). Static points get a
        lower cost than moving ones.
        """
        if radar4d is None or self._active_costmap is None:
            return
        for d_rel, y_rel, radius, cost in points_to_obstacles(radar4d.points):
            # points_to_obstacles costs are 0-1; the cost layer is 0-COST_OBSTACLE
            self._active_costmap.add_obstacle(d_rel, y_rel, 2.0 * radius, 2.0 * radius,
                                              int(round(cost * COST_OBSTACLE)))

    def _fuse_radar2d(self, objects: list, radar2d) -> list:
        """Orchestrate radar2d corner fusion.

        Prefer on-node tracked Radar2DObjects when the ESP32-S3 corner nodes
        publish them; fall back to the legacy zone-presence returns path when
        the object list is empty (presence-only nodes / diagnostic paths).
        """
        if radar2d is None:
            return objects
        if len(radar2d.objects) > 0:
            return self._fuse_radar2d_objects(objects, radar2d)
        return self._fuse_radar2d_returns(objects, radar2d)

    def _fuse_radar2d_objects(self, objects: list, radar2d) -> list:
        """Fuse on-node tracked Radar2DObjects from the ESP32-S3 corner radars.

        Each corner node runs its own Kalman tracker (with occlusion coasting)
        and reports polar tracks in its own frame; we rotate them into the
        vehicle frame with the per-corner mounting pose (self._r2d_corner_pose
        -- the shared registry when available, `_R2D_CORNER_POSE` placeholder
        otherwise, see __init__).
        Coasted tracks (measured=false) are predict-only this frame: they stay
        in the objects list with halved confidence but must not stamp a hard
        obstacle into the costmap.
        """
        for obj_msg in radar2d.objects:
            if obj_msg.corner not in self._r2d_corner_pose:
                # 0xFF = unresolved corner strap (ESP32_RADAR wire_format.h) —
                # without a mounting pose we cannot place the track.
                continue
            d_rel, y_rel = self._corner_local_to_vehicle_frame(
                obj_msg.rangM, obj_msg.azimuthDeg, self._r2d_corner_pose[obj_msg.corner])

            snr_frac = min(obj_msg.snrDb / self._R2D_SNR_REF_DB, 1.0)
            existence_frac = min(max(obj_msg.existenceProb / 100.0, 0.0), 1.0)
            confidence_boost = ((snr_frac + existence_frac) / 2.0) * self._R2D_CONFIDENCE_BOOST
            confidence = 0.5 + confidence_boost
            if not obj_msg.measured:
                # Predict-only coast through occlusion — softer evidence.
                confidence *= 0.5

            objects.append({
                'dRel': d_rel, 'yRel': y_rel, 'vRel': float(obj_msg.vRel),
                'aRel': float(obj_msg.aRel),
                'confidence': confidence, 'prob': confidence,
                'trackId': encode_corner_track_id(obj_msg.corner, obj_msg.trackId),
                'obstacleType': 0,
                'dynProp': int(obj_msg.dynProp),
                'length': float(obj_msg.lengthM),
                'width': float(obj_msg.widthM),
                # Node-computed time-to-collision (NaN = none/unavailable).
                # Carried through unchanged so downstream zone logic can use a
                # real trajectory TTC instead of re-deriving one from
                # dRel/vRel: vRel is RADIAL Doppler, so that division
                # over-alarms on an object merely crossing our line of sight.
                # Only the node holds the Cartesian [vx,vy] that tells the two
                # apart. See radar_zones.corner_ttc_s().
                'ttcS': float(obj_msg.ttcS),
                # Whether that TTC is authoritative — see radar_zones.corner_ttc_s():
                # with this true, an absent TTC means "node cleared it", not
                # "unknown", and must NOT be replaced by a local estimate.
                'ttcValid': bool(obj_msg.ttcValid),
            })

            if obj_msg.measured and self._active_costmap is not None:
                # Heading is unknown: an orientation-free square footprint
                size = 2.0 * self._R2D_ZONE_RADIUS
                if 0.1 < obj_msg.lengthM < 10.0 and 0.1 < obj_msg.widthM < 10.0:
                    size = max(obj_msg.lengthM, obj_msg.widthM)
                self._active_costmap.add_obstacle(d_rel, y_rel, size, size, COST_OBSTACLE)

        return objects

    def _fuse_radar2d_returns(self, objects: list, radar2d) -> list:
        """Map corner-sensor zone presence to costmap obstacles and stereoObjects entries."""
        for r in radar2d.returns:
            if not r.present:
                continue
            d_rel, y_rel = self._R2D_ZONE_POS[r.side]
            v_rel = float(r.vRel) if not math.isnan(float(r.vRel)) else 0.0

            if self._active_costmap is not None:
                size = 2.0 * self._R2D_ZONE_RADIUS
                self._active_costmap.add_obstacle(d_rel, y_rel, size, size, COST_OBSTACLE)

            objects.append({
                'dRel': d_rel, 'yRel': y_rel, 'vRel': v_rel,
                'confidence': self._R2D_PROB, 'prob': self._R2D_PROB,
                'trackId': encode_corner_track_id(r.side, 0), 'obstacleType': 0,
            })
        return objects

    def _merge_detections(self, mono_objects: list, model_objects: list) -> list:
        """Merge mono and modeld detections, deduplicating leads."""
        if not model_objects:
            return mono_objects
        if not mono_objects:
            return model_objects

        merged = list(mono_objects)

        for model_obj in model_objects:
            is_duplicate = False
            for existing in merged:
                # A car under a traffic light is not the light
                if existing['obstacleType'] == 'traffic light':
                    continue
                dist = np.sqrt(
                    (model_obj['dRel'] - existing['dRel'])**2 +
                    (model_obj['yRel'] - existing['yRel'])**2
                )
                if dist < 3.0:
                    if model_obj['confidence'] > existing['confidence']:
                        existing.update(model_obj)
                    is_duplicate = True
                    break

            if not is_duplicate:
                merged.append(model_obj)

        return merged

    # surfaced CellState (surfaced.py): 0 unknown, 1 smooth, 2 normal
    # (= DRIVABLE), 3 rough, 4 learned rough, 255 obstacle. The old mapping
    # read 2 as occupied and turned every normal road cell into an obstacle.
    _SURFACE_COST = np.full(256, COST_UNKNOWN, dtype=np.uint16)
    _SURFACE_COST[[1, 2]] = COST_ROAD
    _SURFACE_COST[[3, 4]] = COST_UNCERTAIN

    def _drivable_area_to_costmap(self, drivable_area) -> tuple[np.ndarray, np.ndarray] | None:
        """surfaced's drivableArea resampled onto BEV_GRID.

        Returns (drivable cost, obstacle mask). surfaced uses the same
        convention (rows forward, cols left positive, originX lateral,
        originY forward), at its own resolution and extent.
        """
        if drivable_area is None:
            return None
        grid_h, grid_w = int(drivable_area.height), int(drivable_area.width)
        grid_data = np.frombuffer(drivable_area.data, dtype=np.uint8)
        if grid_h <= 0 or grid_w <= 0 or len(grid_data) != grid_h * grid_w or drivable_area.resolution <= 0:
            cloudlog.warning(f"DrivableArea size mismatch: {len(grid_data)} vs {grid_h}x{grid_w}")
            return None
        grid = grid_data.reshape((grid_h, grid_w))

        rows, cols = np.indices((BEV_GRID.rows, BEV_GRID.cols))
        forward, left = BEV_GRID.center(rows, cols)
        src_r = np.floor((forward - drivable_area.originY) / drivable_area.resolution).astype(np.int32)
        src_c = np.floor((left - drivable_area.originX) / drivable_area.resolution).astype(np.int32)
        inside = (src_r >= 0) & (src_r < grid_h) & (src_c >= 0) & (src_c < grid_w)

        cells = np.zeros((BEV_GRID.rows, BEV_GRID.cols), dtype=np.uint8)
        cells[inside] = grid[src_r[inside], src_c[inside]]
        return self._SURFACE_COST[cells], cells == 255

    def _extract_road_boundaries(self, xyz_points: np.ndarray | None) -> tuple:
        """Extract road boundaries from point cloud.

        In stereo X (right positive), the frame of modelV2 roadEdges and of
        pathd's path: the left boundary is negative. stereoGround carries them
        so; pathd's lat_nudge reads them in that frame.
        """
        defaults = ([-3.0] * 7, [3.0] * 7)

        if xyz_points is None or len(xyz_points) == 0:
            return defaults

        bins = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
        left_b, right_b = [], []

        for dist in bins:
            in_bin = (xyz_points[:, 2] >= dist - 2.5) & (xyz_points[:, 2] < dist + 2.5)
            if not np.any(in_bin):
                left_b.append(-3.0)
                right_b.append(3.0)
            else:
                x_vals = xyz_points[in_bin, 0]
                left_b.append(float(np.percentile(x_vals, 5)))
                right_b.append(float(np.percentile(x_vals, 95)))

        return left_b, right_b

    def _publish(
        self,
        ts: int,
        xyz_points: np.ndarray | None,
        has_segmentation: bool,
        costmap: np.ndarray | None,
        objects: list,
    ) -> None:
        """Publish all outputs."""
        # gridObjects - BEV occupancy + cost, one layout (BEV_GRID)
        msg = messaging.new_message('gridObjects')
        fill_grid_objects(msg.gridObjects, ts, self.bev.get_grid(), costmap,
                          self.drivable.probability() if has_segmentation else None)
        msg.gridObjects.frameId = self.frame_id
        msg.gridObjects.sourceFlags = 0b00000111 | (0b10000 if objects else 0) | (0b100000 if costmap is not None else 0)

        self.pm.send('gridObjects', msg)

        # stereoGround - for pathd
        left_b, right_b = self._extract_road_boundaries(xyz_points)
        ground_msg = messaging.new_message('stereoGround')
        sg = ground_msg.stereoGround
        sg.timestamp = ts
        sg.leftBoundary = left_b
        sg.rightBoundary = right_b
        sg.minGroundDistance = float(np.percentile(xyz_points[:, 2], 99)) if xyz_points is not None else 100.0
        sg.hasStereoDepth = xyz_points is not None
        sg.hasSegmentation = has_segmentation
        self.pm.send('stereoGround', ground_msg)

        # stereoObjects - fused detections (mono + modeld)
        obj_msg = messaging.new_message('stereoObjects')

        if objects:
            items = obj_msg.stereoObjects.init('objects', len(objects))
            for idx, obj in enumerate(objects):
                items[idx].dRel = obj['dRel']
                items[idx].yRel = obj['yRel']
                items[idx].vRel = obj.get('vRel', 0.0)
                items[idx].aRel = obj.get('aRel', 0.0)
                items[idx].obstacleType = obstacle_type(obj['obstacleType'])
                items[idx].trafficLightState = obj.get('trafficLightState', 0)
                items[idx].trafficLightConfidence = obj.get('trafficLightConfidence', 0.0)
                items[idx].prob = obj.get('confidence', obj.get('prob', 0.5))
                items[idx].trackId = obj.get('trackId', 0)
                items[idx].laneZone = obj.get('laneZone', 0)
        else:
            obj_msg.stereoObjects.init('objects', 0)

        self.pm.send('stereoObjects', obj_msg)

    def _publish_status(self, ts: int, processing_time_ms: float, objects_count: int) -> None:
        """Publish gridStatus with fault tracking."""
        msg = messaging.new_message('gridStatus')
        status = msg.gridStatus

        status.timestamp = ts
        status.enabled = self.enabled
        status.frameId = self.frame_id
        status.processingTimeMs = processing_time_ms
        status.fps = RATE
        status.objectsDetected = objects_count

        # Fault state
        status.fault = self.fault
        status.faultReason = self.fault_reason
        status.consecutiveFailures = self.consecutive_failures
        status.segmentationDegraded = self.segmentation_degraded

        self.pm.send('gridStatus', msg)

    def run(self) -> None:
        """Main loop - fuses stereod, monod, and modeld outputs."""
        if not self.params.get_bool("EOPGridEnabled"):
            cloudlog.info("GridD disabled (EOPGridEnabled=false), exiting")
            return

        cloudlog.info("GridD running (fusing: stereod + monod + modeld)")

        while True:
            loop_start = time.monotonic()
            buf_road = self.vipc_road.recv(timeout_ms=0)
            if buf_road is not None:
                self.road_bgr = _nv12_to_bgr(bytes(buf_road.data), buf_road.width, buf_road.height)

            self.sm.update(0)
            ts = int(time.monotonic() * 1e9)

            # Get stereo depth from stereod (lazy reprojection from 2D disparity)
            xyz_points = None
            if self.sm.updated['stereoDepth']:
                stereo_depth = self.sm['stereoDepth']
                xyz_points = self._lazy_reprojection(stereo_depth)
                if xyz_points is not None:
                    cloudlog.debug(f"Lazy reprojection: {len(xyz_points)} points")

            # Get mono detections from monod
            mono_dets = self.sm['monoDetections'] if self.sm.updated['monoDetections'] else None
            mono_objects = self._fuse_mono_detections(mono_dets, xyz_points)

            # Get drive_vision detections from modeld
            model_v2 = self.sm['modelV2'] if self.sm.updated['modelV2'] else None
            model_objects = self._fuse_modeld_detections(model_v2, xyz_points)

            # Update lane line cache — all 4 lines + road edges for full 8-class zone taxonomy
            if model_v2 is not None:
                cache = lane_cache_from_model(model_v2)
                if cache is not None:
                    self._lane_cache = cache
                else:
                    self._lane_cache['valid'] = False

            # Get drivableArea from surfaced (surface perception with free space)
            drivable_area = self.sm['drivableArea'] if self.sm.updated['drivableArea'] else None

            # Merge all detections
            all_objects = self._merge_detections(mono_objects, model_objects)

            _radar3d_msg = self.sm['radar3d'] if self.sm.updated['radar3d'] else None
            _radar2d_msg = self.sm['radar2d'] if self.sm.updated['radar2d'] else None
            _radar4d_msg = (self.sm['radar4d']
                            if self.sm.updated['radar4d'] and self.sm.valid['radar4d'] else None)
            self._refresh_corner_poses()

            inference_success = True

            # Drivable layer: segd's per-camera card maps, each fused once on
            # arrival; the layer ages and moves with the car every tick
            now = time.monotonic()
            self.drivable.step(now, self._forward_speed())
            self._fuse_drivable_bev()
            has_segmentation = self.drivable.has_evidence(now)
            degraded = not has_segmentation
            if degraded != self.segmentation_degraded:
                (cloudlog.warning if degraded else cloudlog.info)(
                    f"GridD: road segmentation {'degraded (no camera map from segd)' if degraded else 'restored'}")
                self.segmentation_degraded = degraded

            # Stereo occupancy first: the cost layer reads it
            if xyz_points is not None:
                self.bev.update_from_points(xyz_points)

            try:
                if drivable_area is not None:
                    surface = self._drivable_area_to_costmap(drivable_area)
                    if surface is not None:
                        self._surface, self._surface_frame = surface, self.frame_id

                # Cost layer, layers combined by max (FusionCostmap)
                self.costmap_gen.reset()
                if has_segmentation:
                    self.costmap_gen.apply_drivable(self.drivable.cost())
                if self._surface is not None and self.frame_id - self._surface_frame <= SURFACE_MAX_AGE:
                    self.costmap_gen.apply_drivable(self._surface[0])
                    self.costmap_gen.mark_occupied(self._surface[1])
                self.costmap_gen.mark_occupied(self.bev.get_grid() >= OCCUPIED_THRESHOLD)
            except Exception as e:
                cloudlog.error(f"Drivable layer / costmap failed: {e}")
                inference_success = False

            # Radar fusion: 77 GHz long range plus BLE corner Radar2D (stamps
            # measured corner tracks into the cost layer that is published)
            self._active_costmap = self.costmap_gen
            if _radar3d_msg is not None:
                all_objects = self._fuse_radar3d(all_objects, _radar3d_msg)
            if _radar2d_msg is not None:
                all_objects = self._fuse_radar2d(all_objects, _radar2d_msg)
            self._fuse_radar4d(_radar4d_msg)

            # Annotate every fused object with 8-class lane zone (curve-aware, full-taxonomy)
            for obj in all_objects:
                obj['laneZone'] = self._classify_lane_zone(obj['dRel'], obj['yRel'])

            # Calculate processing time
            processing_time_ms = (time.monotonic() - loop_start) * 1000

            # Fault tracking: gridd's own processing raising, not card loss
            if not inference_success:
                self.consecutive_failures += 1
                if self.consecutive_failures >= 3:
                    self.fault = True
                    self.fault_reason = "costmap_consecutive_failures"
                    cloudlog.error(f"GridD FAULT: {self.fault_reason} ({self.consecutive_failures} consecutive failures)")
            else:
                # Reset on success
                if self.consecutive_failures > 0:
                    self.consecutive_failures = 0
                    self.fault = False
                    self.fault_reason = ""
                    cloudlog.info("GridD fault cleared")

            # Publish outputs
            self._publish(ts, xyz_points, has_segmentation, self.costmap_gen.costmap, all_objects)

            # Publish status at 1Hz (every 20 frames at 20Hz)
            if self.frame_id % 20 == 0:
                self._publish_status(ts, processing_time_ms, len(all_objects))

            self.frame_id += 1
            self.rk.keep_time()

    def release(self) -> None:
        """Nothing held: card segmentation goes through inferenced."""


def main() -> int:
    gridd = GridD()
    try:
        logging.basicConfig(level=logging.INFO)
        gridd.run()
        return 0
    except Exception as e:
        cloudlog.exception(f"GridD fatal error: {e}")
        return 1
    finally:
        gridd.release()


if __name__ == "__main__":
    exit(main())
