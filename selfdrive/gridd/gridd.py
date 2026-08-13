#!/usr/bin/env python3
"""
GridD — Lazy BEV Perception Daemon (Vision Layer - CRITICAL)

Consumes 2D stereo outputs and produces BEV occupancy grid for driving.
Performs lazy 3D reprojection - only reprojects pixels needed for BEV.

Inputs (2D from stereod):
  - stereoDepth: 2D disparity map + confidence (NOT 3D points!)
  - stereoDetections: 2D YOLO detections
  - stereoSegments: PP-LiteSeg 19-class masks
  - monoDetections (from monod): Multi-camera YOLO (optional)
  - modelV2 (from modeld): drive_vision leads
  - drivableArea (from surfaced): Surface quality enhancement (optional)

Processing:
  - Lazy reprojection: 2D disparity → 3D points (BEV ROI only)
  - Probabilistic Bayes filter: Temporal occupancy grid
  - Semantic fusion: PP-LiteSeg labels + geometry
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
  - NPU: Reuses stereod PP-LiteSeg outputs (zero cost)

Fault Policy (NO CPU FALLBACK):
  - stereod fault: gridStatus.fault=true → modeld continues (lane keeping only)
  - NPU fail: Continue without segmentation (degraded but safe)
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
import cv2

import cereal.messaging as messaging
from msgq.visionipc import VisionStreamType, VisionIpcClient
from openpilot.common.realtime import Ratekeeper
from openpilot.common.params import Params
from openpilot.common.core_config import set_daemon_affinity

from openpilot.selfdrive.gridd.pp_liteseg import PPLiteSeg
from openpilot.selfdrive.gridd.lazy_bev import LazyBEV
from openpilot.selfdrive.gridd.fusion_costmap import FusionCostmapGenerator, FusionCostmapConfig
from openpilot.selfdrive.modeld.vision.vision_pilot.occupancy_grid_infer import OccupancyGrid
from openpilot.system.hardware.camera_geometry import CameraGeometry
from openpilot.system.hardware.registry import PlatformRegistry
from openpilot.system.inferenced.client import InferenceClient
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware.hw import Paths

RATE = 20  # Hz
PPLITESEG_INPUT_SIZE = (320, 320)  # PP-LiteSeg RKNN model input resolution


def _nv12_to_bgr(data: bytes, width: int, height: int) -> np.ndarray:
    """Convert NV12 to BGR."""
    nv12 = np.frombuffer(data, dtype=np.uint8).reshape((height * 3 // 2, width))
    return cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)


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

        # Radar-stereo geometry for FOV gating and pixel-level association.
        # The BGT60 is mounted at the center of the stereo pair, so its
        # polar detections map directly into the stereo/road camera images.
        # Mounting extrinsics are user-refined and stored at this layer.
        try:
            from openpilot.selfdrive.controls.radar4d_geometry import RadarMounting, RadarStereoGeometry
            self.radar_geometry: RadarStereoGeometry | None = RadarStereoGeometry(mount=RadarMounting.load())
            cloudlog.info("GridD: radar-stereo geometry available")
        except Exception as e:
            self.radar_geometry = None
            cloudlog.warning(f"GridD: radar-stereo geometry unavailable: {e}")

        # Corner-radar mounting poses: prefer the shared vehicle-frame
        # registry (visionpilot WRITES it -- pairing seeds, on-road
        # refines; see radar4d_geometry.load_corner_poses()'s docstring and
        # ESP32_RADAR docs/pairing-and-calibration.md "Unified vehicle-frame
        # extrinsics"), falling back to the class-level placeholder table
        # (_R2D_CORNER_POSE) when the registry is absent or any corner
        # entry is missing/garbled -- load_corner_poses() already returns
        # None wholesale in that case rather than a partially-filled dict,
        # so this is an all-or-nothing swap, never a silent per-corner mix
        # of real and placeholder poses.
        try:
            from openpilot.selfdrive.controls.radar4d_geometry import load_corner_poses
            registry_poses = load_corner_poses()
        except Exception as e:
            registry_poses = None
            cloudlog.warning(f"GridD: corner-radar registry unavailable: {e}")
        if registry_poses is not None:
            self._r2d_corner_pose = registry_poses
            cloudlog.info("GridD: corner-radar poses loaded from shared registry")
        else:
            self._r2d_corner_pose = self._R2D_CORNER_POSE
            cloudlog.info("GridD: corner-radar registry absent/incomplete, using placeholder poses")

        # VisionIPC for road camera
        self.vipc_road = VisionIpcClient("v4l2d", VisionStreamType.VISION_STREAM_ROAD, True)

        # Pub/Sub
        self.pm = messaging.PubMaster(['gridObjects', 'stereoGround', 'stereoObjects', 'gridStatus'])
        self.sm = messaging.SubMaster(
            ['monoDetections', 'monoStatus',
             'stereoDepth', 'stereoStatus', 'stereoDetections',
             'modelV2', 'drivableArea',
             'radar4d',   # BGT60TR13C 4D FMCW — 0-15m front, ±40° azimuth
             'radar3d',   # car OEM CAN radar — 15-200m, all tracked points
             'radar2d'],  # corner/blind-spot zone sensors — 0-10m presence
            poll=cast(str | None, ['stereoDepth'])
        )

        self.rk = Ratekeeper(RATE, print_delay_threshold=None)

        # Perception modules
        self.ppliteseg = PPLiteSeg()
        self.bev = LazyBEV()
        self.occ_grid = OccupancyGrid(range_m=100.0)

        # Fusion costmap generator (GPU with CPU fallback)
        # 60w × 120h cells @ 0.5m/cell = 30m lateral × 60m forward, origin 15m left / 5m behind ego
        costmap_config = FusionCostmapConfig(
            width=60, height=120, resolution=0.5,
            origin_x=-15.0, origin_y=-5.0
        )
        self.costmap_gen = FusionCostmapGenerator(costmap_config)
        cloudlog.info(f"FusionCostmap: GPU={'available' if self.costmap_gen.is_gpu_available() else 'unavailable'}")

        # Live reference to current costmap object for radar fusion methods
        self._active_costmap: FusionCostmapGenerator | None = None

        # Lane line cache — populated each loop from modelV2; used by _ego_lane_bounds() and _classify_lane_zone()
        self._lane_cache: dict = {
            'x': None, 'left_y': None, 'right_y': None,
            'far_left_y': None, 'far_right_y': None,
            'left_edge_y': None, 'right_edge_y': None,
            'valid': False,
        }

        # Road camera frame
        self.road_bgr: np.ndarray | None = None

        # RGA preprocessing for PP-LiteSeg (InferenceClient)
        self._inference_client: InferenceClient | None = None
        self._rga = None
        self._rga_available = False
        try:
            self._inference_client = InferenceClient("gridd")
            self._rga = self._inference_client.rga()
            self._rga_available = True
            cloudlog.info("GridD: RGA preprocessing available (InferenceClient)")
        except Exception as e:
            cloudlog.warning(f"GridD: RGA not available, using OpenCV fallback: {e}")

        # Fault tracking
        self.frame_id = 0
        self.consecutive_failures = 0
        self.fault = False
        self.fault_reason = ""
        self.enabled = True

        cloudlog.info(
            "GridD initialized (ppliteseg=%s, geometry=%s, rga=%s, lazy_bev=enabled)",
            self.ppliteseg.available, self.geometry.variant, self._rga_available
        )

    def _load_calibration(self) -> np.ndarray | None:
        """Load stereo Q matrix for 3D reprojection (factory intrinsics from HAL)."""
        try:
            from hal.drivers.camera import load_stereo_intrinsics
            cal = load_stereo_intrinsics()  # canonical path + legacy migration fallback
            if cal is None:
                return None
            Q = cal.Q
        except ImportError:
            import os
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

    def _preprocess_frame(self, bgr_frame: np.ndarray) -> np.ndarray:
        """
        Preprocess road camera frame for PP-LiteSeg inference.

        Uses RGA hardware accelerator (resize) when available,
        falls back to OpenCV on dev PC.

        PP-LiteSeg RKNN model expects 320x320 input.
        """
        target_w, target_h = PPLITESEG_INPUT_SIZE

        # Fast path: already correct size
        if bgr_frame.shape[1] == target_w and bgr_frame.shape[0] == target_h:
            return bgr_frame

        # Try RGA hardware resize first
        if self._rga_available and self._rga is not None:
            try:
                result = self._rga.infer(
                    'resize',
                    {
                        'input': bgr_frame,
                        'width': target_w,
                        'height': target_h,
                    }
                )
                if result.success and 'output' in result.outputs:
                    output = result.outputs['output']
                    if isinstance(output, np.ndarray):
                        cloudlog.debug(f"RGA resize: {bgr_frame.shape[1]}x{bgr_frame.shape[0]} -> {target_w}x{target_h}")
                        return output
            except Exception as e:
                cloudlog.debug(f"RGA resize failed, falling back to OpenCV: {e}")

        # OpenCV fallback
        return cv2.resize(bgr_frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)

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
            y = det.y  # lateral (m)
            z = det.z  # up (m)

            # Validate with stereo depth if available
            if xyz_points is not None and len(xyz_points) > 0:
                dists = np.abs(xyz_points[:, 2] - x)
                closest_idx = np.argmin(dists)

                if dists[closest_idx] < 5.0:
                    stereo_x = xyz_points[closest_idx, 2]
                    if stereo_x < 30.0:
                        x = 0.7 * stereo_x + 0.3 * x

            fused_objects.append({
                'dRel': float(x),
                'yRel': float(-y),
                'zRel': float(z),
                'obstacleType': det.className.lower(),
                'confidence': det.confidence,
                'trackId': det.trackId,
                'source': det.cameraSource,
                'width': float(getattr(det, 'width', 0.0)),
                'height': float(getattr(det, 'height', 0.0)),
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
                y = lead.y[0]  # lateral (m)
                v = lead.v[0]  # velocity (m/s)
                prob = lead.prob

                # Validate with stereo depth
                confidence = prob
                if xyz_points is not None and len(xyz_points) > 0:
                    dists = np.sqrt(
                        (xyz_points[:, 2] - x)**2 +
                        (xyz_points[:, 0] - (-y))**2
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

    # radar4d fusion constants
    _R4D_BLIND_AZ_DEG    = 20.0   # |azimuth| above this → adjacent lane (blind spot zone)
    _R4D_ASSOC_M         = 2.0    # Cartesian match radius to existing stereo object (m)
    _R4D_ELEV_GATE_DEG   = 15.0   # |elevation| above this → overhead sign / ground bounce, reject
    _R4D_SNR_REF_DB      = 20.0   # SNR reference: car at 10m ≈ 20 dB → max RCS boost
    _R4D_CONFIDENCE_BOOST = 0.15  # max prob boost, blended from SNR/RCS + tracker existenceProb
    _R4D_SKIP_STATIC      = False # keep static tracks; gridd/pathd can filter downstream
    _R4D_USE_FOV_GATE     = True  # reject radar points outside stereo/road camera FOV

    # Autoware radar-in-camera-box velocity fusion defaults (m)
    _R4D_BOX_LEN_M   = 4.5   # longitudinal half-length for leads / cars without size
    _R4D_BOX_WIDTH_M = 1.0   # lateral half-width when no mono size available
    _R4D_BOX_HEIGHT_M= 1.5   # vertical half-height
    _R4D_VEL_OUTLIER_MPS = 3.0  # reject radar points >3 m/s away from median
    _R4D_VEL_ASSOC_GATE_MPS = 3.0  # skip vRel attach when stereo/radar disagree by more (Autoware velocity-consistency gate)

    # radar2d zone positions in vehicle frame (dRel=forward, yRel=left, m)
    _R2D_ZONE_POS = {
        0: ( 2.5,  3.0),   # left-front
        1: (-4.0,  3.0),   # left-rear
        2: ( 2.5, -3.0),   # right-front
        3: (-4.0, -3.0),   # right-rear
    }
    _R2D_ZONE_RADIUS   = 2.0   # costmap obstacle radius (m)
    _R2D_PROB          = 0.92  # hardware detection — high confidence

    # radar2d corner mounting poses in vehicle frame (x_m forward, y_m left, yaw_deg CCW).
    # Keys match ESP32_RADAR radar_corner_id_t: 0=FL, 1=FR, 2=RL, 3=RR.
    # FALLBACK ONLY as of 2026-08-08 -- __init__ prefers the shared
    # vehicle-frame registry (radar4d_geometry.load_corner_poses()) and
    # falls back to this table only when that registry is absent or
    # incomplete; see self._r2d_corner_pose, the actual attribute every
    # consumer reads. PLACEHOLDER values pending extrinsic calibration —
    # same honesty convention as the ESP32_RADAR pins.h placeholders.
    _R2D_CORNER_POSE = {
        0: ( 1.8,  0.8,   45.0),   # front-left
        1: ( 1.8, -0.8,  -45.0),   # front-right
        2: (-1.8,  0.8,  135.0),   # rear-left
        3: (-1.8, -0.8, -135.0),   # rear-right
    }

    # radar3d fusion constants (OEM CAN radar — raw RadarPoint list)
    _R3D_MIN_DREL       = 12.0   # m — below this radar4d has better coverage; skip overlap
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
        """Add OEM CAN radar tracks to stereoObjects.

        Radar3d is a FORWARD-FACING front-bumper radar (dRel > 0 only).
        It cannot see cars approaching from behind — that coverage comes from
        radar2d (corner sensors) and native carState.leftBlindspot/rightBlindspot.

        Use case here: forward adjacent-lane objects (merging traffic, cut-in),
        range 12–200m where radar4d and camera have limited coverage.
        Ego-lane objects are skipped using lane-relative bounds from modelV2 laneLines
        so the gate adapts to curves and S-bends — ACC owns same-lane via radarState.
        """
        for pt in radar3d.points:
            if not pt.measured:              # skip pure tracker extrapolations
                continue
            if pt.dRel <= 0:                 # forward-only radar — skip any stale negative dRel
                continue
            if pt.dRel < self._R3D_MIN_DREL: # let radar4d cover close-range
                continue
            right_y, left_y = self._ego_lane_bounds(pt.dRel)
            if right_y <= pt.yRel <= left_y:  # ego-lane → ACC owns this
                continue

            # Try to associate with an existing stereo/radar4d object
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

    @staticmethod
    def _object_box_m(obj: dict) -> tuple[float, float, float]:
        """Return (length, width, height) in meters for a camera object.

        Uses radar-estimated shape when available (Radar4DObject.lengthM etc.),
        then mono detection size, then class-specific defaults so radar points
        can be gated inside a plausible 3-D box.
        """
        # Radar4DObject fusion writes these keys directly.
        if obj.get('length', 0.0) > 0.0 and obj.get('width', 0.0) > 0.0 and obj.get('height', 0.0) > 0.0:
            return float(obj['length']), float(obj['width']), float(obj['height'])

        obstacle_type = str(obj.get('obstacleType', ''))
        # mono detections may carry width/height (meters); length is rarely
        # estimated by monod, so infer it from the class.
        width = obj.get('width', 0.0)
        height = obj.get('height', 0.0)

        if width > 0.0 and height > 0.0:
            if 'person' in obstacle_type or 'pedestrian' in obstacle_type:
                length = 0.5
                width = max(width, 0.4)
                height = max(height, 1.4)
            elif 'cyclist' in obstacle_type or 'bicycle' in obstacle_type or 'motorcycle' in obstacle_type:
                length = 1.8
                width = max(width, 0.6)
                height = max(height, 1.5)
            elif 'truck' in obstacle_type or 'bus' in obstacle_type:
                length = 8.0
                width = max(width, 2.4)
                height = max(height, 2.8)
            else:  # car / lead / unknown
                length = 4.5
                width = max(width, 1.5)
                height = max(height, 1.4)
            return length, width, height

        # Default boxes by class when no mono size is available.
        defaults = {
            'person': (0.5, 0.5, 1.6),
            'car': (4.5, 1.8, 1.5),
            'lead': (4.5, 1.8, 1.5),
            'truck': (8.0, 2.5, 2.8),
            'bus': (10.0, 2.5, 3.0),
            'cyclist': (1.8, 0.7, 1.6),
            'motorcycle': (2.0, 0.8, 1.5),
        }
        for key, box in defaults.items():
            if key in obstacle_type:
                return box
        return (GridD._R4D_BOX_LEN_M, GridD._R4D_BOX_WIDTH_M * 2.0, GridD._R4D_BOX_HEIGHT_M * 2.0)

    @classmethod
    def _estimate_box_kinematics(cls, obj: dict, points: list) -> tuple[float | None, float | None]:
        """Autoware-style radar-in-camera-box velocity + acceleration estimator.

        Collects radar points that fall inside the object's 3-D bounding box and
        returns robust weighted estimates for vRel and aRel. Aggregating all
        returns belonging to the object is much better than a single nearest-
        point match for sparse CFAR hits in dense traffic.

        Estimator (in order of robustness):
          1. Weighted median of inlier points (robust to occasional clutter).
          2. Weighted average if too few points for a median.
          3. Nearest point as final fallback.

        Weights combine SNR and tracker existence probability. Outliers more
        than _R4D_VEL_OUTLIER_MPS from the median are rejected.
        """
        if not points:
            return None, None

        length, width, height = cls._object_box_m(obj)
        obj_d = obj['dRel']
        obj_y = obj['yRel']
        obj_z = obj.get('zRel', 0.0)

        candidates = []
        for pt in points:
            az = math.radians(pt.azimuth)
            el = math.radians(pt.elevation)
            d_rel = pt.rangM * math.cos(az)
            y_rel = pt.rangM * math.sin(az)
            z_rel = pt.rangM * math.sin(el)

            if (abs(d_rel - obj_d) <= length / 2.0 and
                abs(y_rel - obj_y) <= width / 2.0 and
                abs(z_rel - obj_z) <= height / 2.0):
                weight = max((pt.snrDb / cls._R4D_SNR_REF_DB), 0.1) * max(pt.existenceProb / 100.0, 0.1)
                candidates.append((float(pt.vRel), float(pt.aRel), weight))

        if not candidates:
            # No points inside the camera box — fall back to nearest point.
            pt0 = points[0]
            return float(pt0.vRel), float(pt0.aRel)

        def _weighted_median(values_weights: list[tuple[float, float]]) -> float:
            sorted_cands = sorted(values_weights, key=lambda x: x[0])
            total_w = sum(w for _, w in sorted_cands)
            if total_w <= 0.0:
                return sorted_cands[0][0]
            cum = 0.0
            for v, w in sorted_cands:
                cum += w
                if cum >= total_w / 2.0:
                    return v
            return sorted_cands[-1][0]

        def _weighted_average(values_weights: list[tuple[float, float]]) -> float:
            total_w = sum(w for _, w in values_weights)
            if total_w <= 0.0:
                return values_weights[0][0]
            return sum(v * w for v, w in values_weights) / total_w

        def _robust_estimate(value_weight_list: list[tuple[float, float]]) -> float:
            if len(value_weight_list) >= 3:
                vals = [v for v, _ in value_weight_list]
                median_val = float(np.median(vals))
                inliers = [(v, w) for v, w in value_weight_list
                           if abs(v - median_val) <= cls._R4D_VEL_OUTLIER_MPS]
                if inliers:
                    value_weight_list = inliers
            if len(value_weight_list) >= 3:
                return _weighted_median(value_weight_list)
            return _weighted_average(value_weight_list)

        vel_est = _robust_estimate([(v, w) for v, _, w in candidates])
        # Only use points that reported a valid acceleration estimate.
        accel_cands = [(a, w) for _, a, w in candidates if not math.isnan(a)]
        accel_est = _robust_estimate(accel_cands) if accel_cands else None
        return vel_est, accel_est

    def _radar4d_in_camera_fov(self, rang_m: float, azimuth_deg: float, elevation_deg: float) -> bool:
        """Check if a radar detection falls inside ANY fused camera's FOV.

        The radar data is fused with stereo, road, AND wide cameras, so the
        gate is the union of their fields of view — a return visible only to
        the 150° wide camera (e.g. |azimuth| 30-60°) must be kept.  Only
        returns outside every camera's image are likely extreme side-lobe
        clutter.
        """
        if not self._R4D_USE_FOV_GATE or self.radar_geometry is None:
            return True
        try:
            cams = self.radar_geometry.cam_geo.cameras
            names = [n for n in ('road', 'wide_road') if n in cams]
            if not names:
                # No camera geometry available (e.g. hal not installed) — fail open.
                return True
            return any(
                self.radar_geometry.radar_in_camera_fov(n, rang_m, azimuth_deg, elevation_deg)
                for n in names
            )
        except Exception:
            # If projection fails, keep the point (fail-open).
            return True

    def _fuse_radar4d_objects(self, objects: list, radar4d) -> list:
        """Fuse lidar-style Radar4DObject clusters with stereo objects.

        The radar4d.py pipeline already clustered CFAR hits, removed ground
        returns, and estimated each cluster's length/width/height/yaw.  We use
        that shape for a tighter, orientation-aware association gate instead of
        a plain 2 m circle, and we use the cluster's own Doppler vRel/aRel as
        the velocity estimate (the cluster itself is the "box").

        Two-phase association (Autoware radar_fusion_to_detected_object): a
        large vehicle often splits into several radar clusters that all match
        the same stereo object.  Phase 1 collects every match; phase 2 applies
        only the best-scoring (nearest-to-center) cluster per stereo object
        instead of last-write-wins.
        """
        best_per_stereo: dict[int, tuple[float, Any, float]] = {}  # stereo idx -> (score, obj_msg, confidence_boost)

        for obj_msg in radar4d.objects:
            if abs(obj_msg.elevation) > self._R4D_ELEV_GATE_DEG:
                continue
            if obj_msg.isStatic and self._R4D_SKIP_STATIC:
                continue
            if not self._radar4d_in_camera_fov(obj_msg.rangM, obj_msg.azimuth, obj_msg.elevation):
                continue

            az = math.radians(obj_msg.azimuth)
            d_rel = obj_msg.rangM * math.cos(az)
            y_rel = obj_msg.rangM * math.sin(az)
            z_rel = obj_msg.rangM * math.sin(math.radians(obj_msg.elevation))

            # Shape-aware association gate.  Convert the radar object's oriented
            # box into a normalized distance: <= 1 means the camera object center
            # lies inside the radar box; > 1 means outside.  A loose base gate
            # keeps the fallback when shape is tiny or unreliable.
            half_len = max(obj_msg.lengthM / 2.0, 0.25)
            half_wid = max(obj_msg.widthM / 2.0, 0.25)
            yaw = obj_msg.yawRad
            cos_y, sin_y = math.cos(yaw), math.sin(yaw)

            best_idx, best_score = None, float('inf')
            for i, obj in enumerate(objects):
                dx = obj['dRel'] - d_rel
                dy = obj['yRel'] - y_rel
                dx_local = dx * cos_y + dy * sin_y
                dy_local = -dx * sin_y + dy * cos_y
                score = math.hypot(dx_local / half_len, dy_local / half_wid)
                if score < best_score:
                    best_score, best_idx = score, i

            snr_frac = min(obj_msg.snrDb / self._R4D_SNR_REF_DB, 1.0)
            existence_frac = obj_msg.existenceProb / 100.0
            confidence_boost = ((snr_frac + existence_frac) / 2.0) * self._R4D_CONFIDENCE_BOOST

            if best_idx is not None and best_score <= 1.0:
                prev = best_per_stereo.get(best_idx)
                if prev is None or best_score < prev[0]:
                    best_per_stereo[best_idx] = (best_score, obj_msg, confidence_boost)
            else:
                objects.append({
                    'dRel': d_rel, 'yRel': y_rel, 'zRel': z_rel,
                    'vRel': float(obj_msg.vRel), 'aRel': float(obj_msg.aRel),
                    'confidence': 0.5 + confidence_boost, 'prob': 0.5 + confidence_boost,
                    'trackId': int(obj_msg.trackId), 'obstacleType': 0,
                    'dynProp': int(obj_msg.dynProp),
                    'length': float(obj_msg.lengthM),
                    'width': float(obj_msg.widthM),
                    'height': float(obj_msg.heightM),
                    'yawRad': float(obj_msg.yawRad),
                })

            if abs(obj_msg.azimuth) > self._R4D_BLIND_AZ_DEG and self._active_costmap is not None:
                self._active_costmap.add_obstacle(d_rel, y_rel, radius=1.5, cost=0.8)

        # Phase 2: apply the winning cluster to each matched stereo object.
        for best_idx, (_, obj_msg, confidence_boost) in best_per_stereo.items():
            obj = objects[best_idx]
            # Velocity-consistency gate (Autoware radar_fusion_to_detected_object
            # isVelocityAvailable): if the stereo object already carries a
            # velocity estimate that disagrees wildly with the radar cluster,
            # keep the confidence boost but do not corrupt its vRel.
            v_rel_stereo = obj.get('vRel')
            velocity_consistent = (v_rel_stereo is None or
                                   abs(v_rel_stereo - obj_msg.vRel) <= self._R4D_VEL_ASSOC_GATE_MPS)
            if velocity_consistent:
                obj['vRel'] = float(obj_msg.vRel)
                obj['aRel'] = float(obj_msg.aRel)
                obj['dynProp'] = max(obj.get('dynProp', 0), int(obj_msg.dynProp))
                # Merge shape into the stereo object so downstream pathd/selfdrived
                # can use the radar-estimated footprint.
                if obj_msg.lengthM > 0.1:
                    obj['length'] = float(obj_msg.lengthM)
                if obj_msg.widthM > 0.1:
                    obj['width'] = float(obj_msg.widthM)
                if obj_msg.heightM > 0.1:
                    obj['height'] = float(obj_msg.heightM)
                if obj_msg.yawRad != 0.0 or obj_msg.lengthM > obj_msg.widthM:
                    obj['yawRad'] = float(obj_msg.yawRad)
            obj['confidence'] = min(obj.get('confidence', 0.5) + confidence_boost, 1.0)

        return objects

    def _merge_radar4d_dropoff(self, objects: list, radar4d) -> list:
        """Merge the radar drop-off guard as a hard-limit obstacle.

        Below-road returns ahead mean the road does not continue (cliff edge,
        ditch, collapsed shoulder).  Camera road-surface detection can miss
        this at night or in glare, so the radar guard enters the costmap at
        maximum cost across the corridor and as a fused object downstream
        planners cannot miss.
        """
        if not getattr(radar4d, 'dropOffHazard', False):
            return objects
        dist = float(radar4d.dropOffDistM)
        if dist <= 0.0:
            return objects
        objects.append({
            'dRel': dist, 'yRel': 0.0, 'zRel': -1.0,
            'vRel': 0.0, 'aRel': 0.0,
            'confidence': 0.95, 'prob': 0.95,
            'trackId': 0, 'obstacleType': 'dropoff',
            'dynProp': 0,
        })
        if self._active_costmap is not None:
            # Hard limit across the forward corridor, not a single cell.
            for y in (-2.0, -1.0, 0.0, 1.0, 2.0):
                self._active_costmap.add_obstacle(dist, y, radius=1.5, cost=1.0)
        return objects

    def _fuse_radar4d_points(self, objects: list, radar4d) -> list:
        """Associate raw BGT60TR13C detections with stereo objects; add unmatched as new entries.

        Forward (|az|<20°): annotates stereo objects with Doppler vRel + boosted prob
        (blended from SNR/RCS and the tracker's confirm-hit-streak existenceProb — a
        confirmed multi-frame track is stronger evidence than one high-SNR return).
        Adjacent-lane (|az|>20°): also marks costmap blind-spot zone.
        Points with |elevation| beyond _R4D_ELEV_GATE_DEG are rejected outright —
        overhead signage / ground-bounce clutter a 2-D-only pipeline can't otherwise
        distinguish from a real forward obstacle.

        New: dynProp/isStatic/aRel are forwarded to gridd objects; velocity and
        acceleration are estimated with an Autoware-style camera-box aggregator;
        forward roadside clutter outside modelV2 lane bounds is dropped to reduce
        phantom obstacles in messy traffic.
        """
        # Per-object list of matched radar points for box velocity estimation.
        matched_points: list[list] = [[] for _ in objects]

        for pt in radar4d.points:
            if abs(pt.elevation) > self._R4D_ELEV_GATE_DEG:
                continue
            if pt.isStatic and self._R4D_SKIP_STATIC:
                continue
            if not self._radar4d_in_camera_fov(pt.rangM, pt.azimuth, pt.elevation):
                continue

            az = math.radians(pt.azimuth)
            d_rel = pt.rangM * math.cos(az)   # forward (+)
            y_rel = pt.rangM * math.sin(az)   # lateral left (+)

            best_idx, best_dist = None, float('inf')
            for i, obj in enumerate(objects):
                d = math.hypot(obj['dRel'] - d_rel, obj['yRel'] - y_rel)
                if d < best_dist:
                    best_dist, best_idx = d, i

            snr_frac = min(pt.snrDb / self._R4D_SNR_REF_DB, 1.0)
            existence_frac = pt.existenceProb / 100.0
            confidence_boost = ((snr_frac + existence_frac) / 2.0) * self._R4D_CONFIDENCE_BOOST

            if best_idx is not None and best_dist < self._R4D_ASSOC_M:
                matched_points[best_idx].append(pt)
                obj = objects[best_idx]
                obj['vRel'] = float(pt.vRel)
                obj['aRel'] = float(pt.aRel)
                obj['confidence'] = min(obj.get('confidence', 0.5) + confidence_boost, 1.0)
                # dynProp: keep the most dynamic label seen for this object.
                obj['dynProp'] = max(obj.get('dynProp', 0), int(pt.dynProp))
            else:
                objects.append({
                    'dRel': d_rel, 'yRel': y_rel, 'vRel': float(pt.vRel),
                    'aRel': float(pt.aRel),
                    'confidence': 0.5 + confidence_boost, 'prob': 0.5 + confidence_boost,
                    'trackId': int(pt.trackId), 'obstacleType': 0,
                    'dynProp': int(pt.dynProp),
                })
                matched_points.append([])

            if abs(pt.azimuth) > self._R4D_BLIND_AZ_DEG and self._active_costmap is not None:
                self._active_costmap.add_obstacle(d_rel, y_rel, radius=1.5, cost=0.8)

        # Second pass: Autoware-style camera-box velocity/acceleration fusion.
        # Replacing the single-point values with robust estimates over all radar
        # returns inside the object's 3-D bounding box reduces noise and clutter
        # contamination — critical for smooth braking/acceleration in dense traffic.
        for i, obj in enumerate(objects):
            pts = matched_points[i]
            if not pts:
                continue
            box_vel, box_accel = self._estimate_box_kinematics(obj, pts)
            if box_vel is not None:
                obj['vRel'] = box_vel
            if box_accel is not None:
                obj['aRel'] = box_accel

        return objects

    def _fuse_radar4d(self, objects: list, radar4d) -> list:
        """Orchestrate radar4d fusion.

        Prefer lidar-style Radar4DObject clusters when radar4d.py publishes them;
        they carry shape and already aggregated Doppler.  Fall back to raw points
        for legacy / diagnostic paths or when clustering yielded nothing.
        """
        if radar4d is None:
            return objects
        if len(radar4d.objects) > 0:
            return self._fuse_radar4d_objects(objects, radar4d)
        return self._fuse_radar4d_points(objects, radar4d)

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
            px, py, yaw_deg = self._r2d_corner_pose[obj_msg.corner]
            az = math.radians(obj_msg.azimuthDeg)
            yaw = math.radians(yaw_deg)
            sx = obj_msg.rangM * math.cos(az)   # forward in sensor frame
            sy = obj_msg.rangM * math.sin(az)   # left in sensor frame
            d_rel = px + sx * math.cos(yaw) - sy * math.sin(yaw)
            y_rel = py + sx * math.sin(yaw) + sy * math.cos(yaw)

            snr_frac = min(obj_msg.snrDb / self._R4D_SNR_REF_DB, 1.0)
            existence_frac = min(max(obj_msg.existenceProb / 100.0, 0.0), 1.0)  # 0-100, same convention as Radar4DObject
            confidence_boost = ((snr_frac + existence_frac) / 2.0) * self._R4D_CONFIDENCE_BOOST
            confidence = 0.5 + confidence_boost
            if not obj_msg.measured:
                # Predict-only coast through occlusion — softer evidence.
                confidence *= 0.5

            objects.append({
                'dRel': d_rel, 'yRel': y_rel, 'vRel': float(obj_msg.vRel),
                'aRel': float(obj_msg.aRel),
                'confidence': confidence, 'prob': confidence,
                'trackId': int(obj_msg.trackId), 'obstacleType': 0,
                'dynProp': int(obj_msg.dynProp),
                'length': float(obj_msg.lengthM),
                'width': float(obj_msg.widthM),
            })

            if obj_msg.measured and self._active_costmap is not None:
                radius = self._R2D_ZONE_RADIUS
                if 0.1 < obj_msg.lengthM < 10.0 and 0.1 < obj_msg.widthM < 10.0:
                    radius = max(obj_msg.lengthM, obj_msg.widthM) / 2.0
                self._active_costmap.add_obstacle(d_rel, y_rel, radius=radius, cost=0.9)

        return objects

    def _fuse_radar2d_returns(self, objects: list, radar2d) -> list:
        """Map corner-sensor zone presence to costmap obstacles and stereoObjects entries."""
        for r in radar2d.returns:
            if not r.present:
                continue
            d_rel, y_rel = self._R2D_ZONE_POS[r.side]
            v_rel = float(r.vRel) if not math.isnan(float(r.vRel)) else 0.0

            if self._active_costmap is not None:
                self._active_costmap.add_obstacle(d_rel, y_rel,
                                                  radius=self._R2D_ZONE_RADIUS, cost=0.9)

            objects.append({
                'dRel': d_rel, 'yRel': y_rel, 'vRel': v_rel,
                'confidence': self._R2D_PROB, 'prob': self._R2D_PROB,
                'trackId': 0, 'obstacleType': 0,
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

    def _costmap(
        self,
        xyz_points: np.ndarray,
        seg_mask: np.ndarray,
        lane_lines,
        img_shape: tuple,
    ) -> np.ndarray:
        """Generate semantic costmap - VisionPilot-style fusion.

        Uses FusionCostmapGenerator with GPU-assigned compute.

        Cost values:
          0   = Lane marking (preferred path)
          10  = Confident road (geometry + semantics agree)
          25  = Uncertain road (geometry only)
          50  = Unknown / no data
          100 = Obstacle (non-road semantic class)
        """
        return self.costmap_gen.fuse_pointcloud(xyz_points)

    def _drivable_area_to_costmap(self, drivable_area) -> np.ndarray | None:
        """
        Convert drivableArea from surfaced to gridd costmap format.

        drivableArea provides:
        - BEV occupancy grid
        - Precise pose (road frame)
        - Clearances (left/right/front)
        - Surface quality

        We convert this to the costmap format expected by pathd.
        """
        if drivable_area is None:
            return None

        # Get grid dimensions from drivableArea
        # capnp fields: width, height, resolution (not rows/cols/resolutionM)
        grid_data = np.frombuffer(drivable_area.data, dtype=np.uint8)
        grid_h = drivable_area.height
        grid_w = drivable_area.width

        if len(grid_data) != grid_h * grid_w:
            cloudlog.warning(f"DrivableArea size mismatch: {len(grid_data)} vs {grid_h}x{grid_w}")
            return None

        # Reshape to 2D grid
        grid = grid_data.reshape((grid_h, grid_w))

        # Convert cell values to costmap
        # drivableArea cell values (from surfaced):
        #   0 = CELL_UNKNOWN
        #   1 = CELL_DRIVABLE  (smooth)
        #   2 = CELL_OCCUPIED  (obstacle)
        #   3 = CELL_ROUGH     (rough, current frame)
        #   4 = CELL_LEARNED_ROUGH (rough, from history)
        # costmap output: 0=free, 25=rough, 50=unknown, 100=obstacle
        costmap = np.full((grid_h, grid_w), 50, dtype=np.uint8)  # default: unknown
        costmap[grid == 1] = 0    # drivable → free
        costmap[grid == 2] = 100  # occupied → obstacle
        costmap[grid == 3] = 25   # rough → elevated cost
        costmap[grid == 4] = 25   # learned rough → elevated cost
        # 0 (unknown) stays at 50

        # Apply clearances as soft constraints
        if drivable_area.leftClearanceM > 0:
            # Mark area beyond left clearance as higher cost
            left_cells = int(drivable_area.leftClearanceM / drivable_area.resolution)
            if left_cells < grid_w // 2:
                costmap[:, :grid_w//2 - left_cells] = np.minimum(
                    costmap[:, :grid_w//2 - left_cells] + 25, 100
                )

        if drivable_area.rightClearanceM > 0:
            right_cells = int(drivable_area.rightClearanceM / drivable_area.resolution)
            if right_cells < grid_w // 2:
                costmap[:, grid_w//2 + right_cells:] = np.minimum(
                    costmap[:, grid_w//2 + right_cells:] + 25, 100
                )

        # Apply surface quality as cost multiplier
        if drivable_area.hasSurfaceQuality:
            quality = drivable_area.surfaceQuality.score
            if quality > 0.3:
                # Rough surface -> increase cost
                costmap = np.minimum(costmap + int(quality * 30), 100)

        return costmap

    def _extract_road_boundaries(self, xyz_points: np.ndarray | None) -> tuple:
        """Extract road boundaries from point cloud."""
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
        road_mask: np.ndarray | None,
        costmap: np.ndarray | None,
        objects: list,
    ) -> None:
        """Publish all outputs."""
        # gridObjects - BEV occupancy + semantic costmap
        msg = messaging.new_message('gridObjects')
        g = msg.gridObjects
        g.timestamp = ts
        g.resolution = self.bev.resolution_m
        g.width = self.bev.grid_w
        g.height = self.bev.grid_h
        g.originX = -self.bev.half_w * self.bev.resolution_m
        g.originY = 0.0
        g.sourceFlags = 0b00000111 | (0b10000 if objects else 0) | (0b100000 if costmap is not None else 0)

        # Calculate number of layers
        num_layers = 1  # Occupancy
        if costmap is not None:
            num_layers = 2  # Add costmap layer

        layers = g.init('layers', num_layers)

        # Layer 0: occupancy
        grid = self.bev.get_grid()
        if grid is not None:
            occ_uint8 = (np.clip(grid, 0.0, 1.0) * 255).astype(np.uint8)
            layers[0].name = "occupancy"
            layers[0].encoding = 0
            layers[0].data = occ_uint8.tobytes()
            layers[0].scale = 1.0 / 255.0

        # Layer 1: semantic costmap (if available)
        if costmap is not None and num_layers > 1:
            layers[1].name = "semantic_costmap"
            layers[1].encoding = 1  # 1=cost (uint8 cost values: 0, 10, 25, 50, 100)
            layers[1].data = costmap.tobytes()
            layers[1].scale = 1.0  # Cost values are direct (not scaled)

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
        sg.hasSegmentation = road_mask is not None
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
                items[idx].obstacleType = obj['obstacleType']
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

        self.pm.send('gridStatus', msg)

    def run(self) -> None:
        """Main loop - fuses stereod, monod, and modeld outputs."""
        if not self.params.get_bool("EOPGridEnabled"):
            cloudlog.info("GridD disabled (EOPGridEnabled=false), exiting")
            return

        cloudlog.info("Connecting to VisionIPC (road)...")
        while not self.vipc_road.connect(False):
            time.sleep(0.1)

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
                ll  = model_v2.laneLines
                lp  = model_v2.laneLineProbs
                re  = model_v2.roadEdges
                if (len(ll) >= 4 and len(lp) >= 4
                        and lp[1] > 0.3 and lp[2] > 0.3):
                    ll0_y0 = ll[0].y[0] if len(ll[0].y) > 0 else 0.0
                    ll1_y0 = ll[1].y[0] if len(ll[1].y) > 0 else 0.0
                    ll2_y0 = ll[2].y[0] if len(ll[2].y) > 0 else 0.0
                    ll3_y0 = ll[3].y[0] if len(ll[3].y) > 0 else 0.0
                    # Sanity-check ordering: far-left must be more-positive-y than left boundary
                    far_left_valid  = lp[0] > 0.2 and ll0_y0 > ll1_y0
                    far_right_valid = lp[3] > 0.2 and ll3_y0 < ll2_y0
                    self._lane_cache = {
                        'x':             list(ll[1].x),
                        'left_y':        list(ll[1].y),
                        'right_y':       list(ll[2].y),
                        'far_left_y':    list(ll[0].y) if far_left_valid  else None,
                        'far_right_y':   list(ll[3].y) if far_right_valid else None,
                        'left_edge_y':   list(re[0].y) if len(re) > 0 else None,
                        'right_edge_y':  list(re[1].y) if len(re) > 1 else None,
                        'valid':         True,
                    }
                else:
                    self._lane_cache['valid'] = False

            # Get drivableArea from surfaced (surface perception with free space)
            drivable_area = self.sm['drivableArea'] if self.sm.updated['drivableArea'] else None

            # Merge all detections
            all_objects = self._merge_detections(mono_objects, model_objects)

            # Radar fusion — must run after costmap is available (set below)
            # _active_costmap is set once costmap is computed so fusion can mark it
            _radar4d_msg = self.sm['radar4d'] if self.sm.updated['radar4d'] else None
            _radar3d_msg = self.sm['radar3d'] if self.sm.updated['radar3d'] else None
            _radar2d_msg = self.sm['radar2d'] if self.sm.updated['radar2d'] else None

            # Segmentation - PP-LiteSeg with full semantic costmap
            # PP-LiteSeg (19-class Cityscapes) class 0=road, 1=sidewalk
            road_mask = None
            costmap = None
            inference_success = True

            if self.ppliteseg.available and self.road_bgr is not None:
                try:
                    # Preprocess frame to PP-LiteSeg input size (320x320)
                    # Uses RGA hardware resize when available, OpenCV fallback on dev PC
                    preprocessed = self._preprocess_frame(self.road_bgr)
                    seg_mask = self.ppliteseg.infer(preprocessed)  # Returns 19-class mask
                    if seg_mask is None:
                        raise RuntimeError("PP-LiteSeg returned no segmentation mask")
                    # Extract road pixels (class 0=road, 1=sidewalk)
                    road_mask = ((seg_mask == 0) | (seg_mask == 1)).astype(np.uint8) * 255

                    # Use drivableArea from surfaced if available (preferred)
                    # Otherwise fall back to computed costmap
                    if drivable_area is not None:
                        # Convert drivableArea to costmap format
                        costmap = self._drivable_area_to_costmap(drivable_area)
                    elif xyz_points is not None:
                        lane_lines = model_v2.laneLines if model_v2 is not None else None
                        costmap = self._costmap(
                            xyz_points, seg_mask, lane_lines,
                            (self.road_bgr.shape[0], self.road_bgr.shape[1])
                        )
                except Exception as e:
                    cloudlog.error(f"PP-LiteSeg inference failed: {e}")
                    inference_success = False

            # Radar fusion: order matters — radar4d close range first, then radar3d far range
            self._active_costmap = self.costmap_gen
            if _radar4d_msg is not None:
                all_objects = self._fuse_radar4d(all_objects, _radar4d_msg)
                all_objects = self._merge_radar4d_dropoff(all_objects, _radar4d_msg)
            if _radar3d_msg is not None:
                all_objects = self._fuse_radar3d(all_objects, _radar3d_msg)
            if _radar2d_msg is not None:
                all_objects = self._fuse_radar2d(all_objects, _radar2d_msg)

            # Annotate every fused object with 8-class lane zone (curve-aware, full-taxonomy)
            for obj in all_objects:
                obj['laneZone'] = self._classify_lane_zone(obj['dRel'], obj['yRel'])

            # Update BEV
            if xyz_points is not None:
                self.bev.update_from_points(xyz_points)

            # Calculate processing time
            processing_time_ms = (time.monotonic() - loop_start) * 1000

            # Fault tracking: count consecutive NPU inference failures
            if not inference_success:
                self.consecutive_failures += 1
                if self.consecutive_failures >= 3:
                    self.fault = True
                    self.fault_reason = "npu_consecutive_failures"
                    cloudlog.error(f"GridD FAULT: {self.fault_reason} ({self.consecutive_failures} consecutive failures)")
            else:
                # Reset on success
                if self.consecutive_failures > 0:
                    self.consecutive_failures = 0
                    self.fault = False
                    self.fault_reason = ""
                    cloudlog.info("GridD fault cleared (NPU recovered)")

            # Publish outputs
            self._publish(ts, xyz_points, road_mask, costmap, all_objects)

            # Publish status at 1Hz (every 20 frames at 20Hz)
            if self.frame_id % 20 == 0:
                self._publish_status(ts, processing_time_ms, len(all_objects))

            self.frame_id += 1
            self.rk.keep_time()

    def release(self) -> None:
        """Release HAL resources."""
        if self._inference_client is not None:
            try:
                self._inference_client.release()
            except Exception:
                pass
            self._inference_client = None
        self._rga = None


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
