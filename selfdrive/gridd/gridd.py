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
from openpilot.system.inferenced.compute import BackendType
from openpilot.selfdrive.sided.egpu_camera_detector import (
    EgpuSegmentationShadowRunner, is_backend_available
)
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware.hw import Paths
from openpilot.selfdrive.controls.radar_corner_geometry import (
    corner_local_to_vehicle_frame, encode_corner_track_id, load_corner_poses)

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

        # BLE Radar2D uses the pose jointly calibrated by the ESP32 and host.
        # already levels BLE tracks with roll/pitch; this host step applies
        # the remaining per-corner yaw and XY translation. Approximate
        # installation priors are calibration/display aids, never ADAS input.
        self._corner_local_to_vehicle_frame = corner_local_to_vehicle_frame
        self._r2d_corner_pose = {}
        self._corner_pose_reload_t = -1.0
        self._corner_pose_warn_t = -30.0
        self._refresh_corner_poses(force=True)

        # VisionIPC for road camera
        self.vipc_road = VisionIpcClient("v4l2d", VisionStreamType.VISION_STREAM_ROAD, True)

        # Pub/Sub
        self.pm = messaging.PubMaster(['gridObjects', 'stereoGround', 'stereoObjects', 'gridStatus'])
        self.sm = messaging.SubMaster(
            ['monoDetections', 'monoStatus',
             'stereoDepth', 'stereoStatus', 'stereoDetections',
             'modelV2', 'drivableArea',
             'radar3d',   # long-range UART radar — 15-200m, all tracked points
             'radar2d'],  # corner/blind-spot zone sensors — 0-10m presence
            poll=cast(str | None, ['stereoDepth'])
        )

        self.rk = Ratekeeper(RATE, print_delay_threshold=None)

        # Perception modules
        self.ppliteseg = PPLiteSeg()
        self.bev = LazyBEV()
        self.occ_grid = OccupancyGrid(range_m=100.0)

        # Optional eGPU segmentation shadow for front road camera.
        # Created only when EOPFrontRoadSegEGPUMode=shadow AND inferenced advertises EGPU.
        self._egpu_road_seg_shadow: EgpuSegmentationShadowRunner | None = None
        self._init_egpu_road_seg_shadow()

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

    def _init_egpu_road_seg_shadow(self) -> None:
        """Create the front-road eGPU segmentation shadow runner if enabled and available."""
        mode = (self.params.get("EOPFrontRoadSegEGPUMode") or b"off").decode().strip().lower()
        if mode != "shadow":
            return
        if not is_backend_available(BackendType.EGPU, timeout=0.5, client=self._inference_client):
            cloudlog.warning("GridD: EOPFrontRoadSegEGPUMode=shadow but EGPU backend not advertised")
            return
        self._egpu_road_seg_shadow = EgpuSegmentationShadowRunner(
            daemon_name="gridd",
            model_name="front_road_seg_egpu",
            input_size=PPLITESEG_INPUT_SIZE,
            class_interest={0, 1},  # road + sidewalk
            client=self._inference_client,
        )
        cloudlog.info("GridD: front-road eGPU segmentation shadow enabled")

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
            _radar3d_msg = self.sm['radar3d'] if self.sm.updated['radar3d'] else None
            _radar2d_msg = self.sm['radar2d'] if self.sm.updated['radar2d'] else None
            self._refresh_corner_poses()

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

            # Front-road eGPU segmentation shadow (optional; authoritative PP-LiteSeg result unchanged)
            if self._egpu_road_seg_shadow is not None and self.road_bgr is not None:
                self._egpu_road_seg_shadow.submit('road', self.road_bgr, road_mask)

            # Radar fusion: 77 GHz long range plus BLE corner Radar2D.
            self._active_costmap = self.costmap_gen
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
        if self._egpu_road_seg_shadow is not None:
            try:
                self._egpu_road_seg_shadow.close()
            except Exception:
                pass
            self._egpu_road_seg_shadow = None
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
