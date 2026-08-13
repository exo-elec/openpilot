#!/usr/bin/env python3
"""
PointcloudD — 3D Reconstruction Daemon (NON-CRITICAL)

Consumes 2D stereo outputs from stereod, performs GPU-accelerated 3D reconstruction
for fleet digital twin. If this daemon fails, core vision (stereod→gridd→controlsd)
continues driving safely.

Pipeline:
  stereod (2D disparity) ──► pointcloudd (3D reconstruct) ──┬──► surfaced (SQSC)
                                                            ├──► PCD files (fleet)
                                                            └──► pointcloudStatus

Architecture (SoC Platform - No PCIe/Discrete GPU):
  ┌─────────┐     ┌─────────────┐     ┌──────────────┐
  │ stereod │────►│ pointcloudd │────►│   surfaced   │──► gridd
  │ (2D)    │     │  (3D GPU)   │     │ (BEV + SQSC) │
  └─────────┘     └──────┬──────┘     └──────────────┘
                         │
                         ▼
                  ┌─────────────┐
                  │  PCD files  │──► Fleet upload (optional)
                  └─────────────┘

Hardware (SoC Integrated):
  - GPU (Mali OpenCL): 3D reprojection from disparity
  - RGA (2D accel): Format conversion, crop/resize
  - NPU: Zero cost (reuses stereod outputs)
  - NO CPU FALLBACK: CPU is 100% allocated to ADAS tasks

Functions:
  1. Subscribe to stereoDepth (2D disparity + confidence)
  2. Subscribe to stereoSegments (PP-LiteSeg masks)
  3. GPU 3D reprojection (disparity → XYZ via OpenCL on Mali)
  4. Semantic fusion (2D labels → 3D points)
  5. Semantic filtering (remove vegetation, dynamic objects)
  6. Adaptive voxel downsampling (Pandar QT64-style)
  7. Feature extraction (poles, corners, curbs)
  8. Publish to surfaced + save PCD to SD card

Fault Handling:
  - GPU init fail: Continue with CPU (non-critical daemon)
  - GPU runtime fail: Skip frame, retry next
  - No IMMEDIATE_DISABLE (non-critical for driving)
"""

from __future__ import annotations

import os
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

import numpy as np
import cv2

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.selfdrive.stereod.semantic_fusion import SemanticFusion
from openpilot.selfdrive.stereod.yolo_3d import YOLO3DEstimator
from openpilot.selfdrive.pointcloudd.semantic_filter import (
    SemanticPointFilter, FilterConfig
)
from openpilot.selfdrive.pointcloudd.adaptive_voxel import AdaptiveVoxelFilter
from openpilot.selfdrive.pointcloudd.feature_extractor import SemanticFeatureExtractor
from openpilot.common.swaglog import cloudlog

# Centralized HAL - ONLY way to access hardware
from openpilot.system.inferenced import InferenceClient
from openpilot.system.hardware.hw import Paths

POINTCLOUD_ROOT = Path(os.getenv("POINTCLOUD_ROOT", "/data/media/0/pointclouds"))
CALIBRATION_PATH = Path(Paths.eop_data_root()) / "calibration" / "stereo_intrinsics.npz"
DEFAULT_RATE_HZ = 5
DEFAULT_MAX_GB = 4.0


@dataclass
class ReconstructionStats:
    """Statistics for reconstruction pipeline."""
    frames_processed: int = 0
    frames_dropped: int = 0
    avg_recon_time_ms: float = 0.0
    avg_filter_time_ms: float = 0.0
    avg_total_time_ms: float = 0.0
    gpu_accelerated: bool = False
    rga_accelerated: bool = False
    last_errors: list[str] = field(default_factory=list)

    def update_timing(self, recon_ms: float, filter_ms: float, total_ms: float):
        """Update moving average timing stats."""
        alpha = 0.1  # EMA factor
        self.avg_recon_time_ms = (1 - alpha) * self.avg_recon_time_ms + alpha * recon_ms
        self.avg_filter_time_ms = (1 - alpha) * self.avg_filter_time_ms + alpha * filter_ms
        self.avg_total_time_ms = (1 - alpha) * self.avg_total_time_ms + alpha * total_ms

    def add_error(self, error: str):
        """Add error to recent errors list."""
        self.last_errors.append(f"{time.strftime('%H:%M:%S')}: {error}")
        if len(self.last_errors) > 10:
            self.last_errors.pop(0)


class PcdWriter:
    """Thread-safe PCD v0.7 binary writer with semantic labels."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frames_written = 0

    def write(
        self,
        path: Path,
        points: np.ndarray,
        labels: np.ndarray | None,
        features: dict[str, Any | None],
        frame_id: int,
        timestamp_ns: int,
        geo_lat: float = 0.0,
        geo_lon: float = 0.0,
        geo_alt: float = 0.0,
        geo_heading: float = 0.0,
        geo_source: str = "none"
    ) -> bool:
        """Write binary PCD file with optional semantic labels and features."""
        with self._lock:
            try:
                n = len(points)
                has_labels = labels is not None and len(labels) == n

                # Build header
                fields = "x y z"
                sizes = "4 4 4"
                types = "F F F"
                counts = "1 1 1"

                if has_labels:
                    fields += " label"
                    sizes += " 1"
                    types += " U"
                    counts += " 1"

                header_lines = [
                    "# .PCD v0.7 - Point Cloud Data file format",
                    f"# Frame: {frame_id}",
                    f"# Timestamp: {timestamp_ns}",
                    "# Frame ID: base_link",
                    f"# GeoSource: {geo_source}",
                    f"# GeoLat: {geo_lat:.8f}",
                    f"# GeoLon: {geo_lon:.8f}",
                    f"# GeoAlt: {geo_alt:.2f}",
                    f"# GeoHeading: {geo_heading:.4f}",
                    "VERSION 0.7",
                    f"FIELDS {fields}",
                    f"SIZE {sizes}",
                    f"TYPE {types}",
                    f"COUNT {counts}",
                    f"WIDTH {n}",
                    "HEIGHT 1",
                    "VIEWPOINT 0 0 0 1 0 0 0",
                    f"POINTS {n}",
                    "DATA binary",
                ]

                # Add feature metadata as comments
                if features:
                    header_lines.append(f"# Features: {features}")

                header = "\n".join(header_lines) + "\n"

                # Pack data efficiently
                if has_labels:
                    structured = np.zeros(n, dtype=[
                        ('x', np.float32), ('y', np.float32),
                        ('z', np.float32), ('label', np.uint8)
                    ])
                    structured['x'] = points[:, 0]
                    structured['y'] = points[:, 1]
                    structured['z'] = points[:, 2]
                    structured['label'] = labels
                    binary_data = structured.tobytes()
                else:
                    binary_data = points.astype(np.float32).tobytes()

                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(header.encode("ascii"))
                    f.write(binary_data)

                self._frames_written += 1
                return True

            except Exception as e:
                cloudlog.error(f"PCD write failed: {e}")
                return False

    @property
    def frames_written(self) -> int:
        return self._frames_written


class PcdReader:
    """PCD v0.7 binary reader for historical point cloud loading."""

    def read(self, path: Path) -> tuple[np.ndarray, np.ndarray | None, dict[str, Any]] | None:
        """
        Read binary PCD file.

        Returns:
            (points, labels, metadata) or None if failed
            points: (N, 3) float32 array
            labels: (N,) uint8 array or None
            metadata: dict with frame_id, timestamp, etc.
        """
        try:
            with open(path, "rb") as f:
                # Parse header
                header_lines = []
                while True:
                    line = f.readline().decode("ascii")
                    if not line:
                        break
                    line = line.strip()
                    if line == "DATA binary":
                        break
                    header_lines.append(line)

                # Parse fields from header
                fields = []
                sizes = []
                types = []
                counts = []
                width = 0
                metadata = {}

                for line in header_lines:
                    if line.startswith("FIELDS "):
                        fields = line[7:].split()
                    elif line.startswith("SIZE "):
                        sizes = [int(x) for x in line[5:].split()]
                    elif line.startswith("TYPE "):
                        types = line[5:].split()
                    elif line.startswith("COUNT "):
                        counts = [int(x) for x in line[6:].split()]
                    elif line.startswith("WIDTH "):
                        width = int(line[6:])
                    elif line.startswith("# Frame: "):
                        metadata["frame_id"] = int(line[9:])
                    elif line.startswith("# Timestamp: "):
                        metadata["timestamp"] = int(line[13:])

                if width == 0 or not fields:
                    cloudlog.warning(f"Invalid PCD header in {path}")
                    return None

                # Build dtype for structured array
                dtype_list = []
                for field, _size, typ, _count in zip(fields, sizes, types, counts, strict=False):
                    if typ == "F":
                        dtype_list.append((field, np.float32))
                    elif typ == "U":
                        dtype_list.append((field, np.uint8))
                    elif typ == "I":
                        dtype_list.append((field, np.int32))

                # Read binary data
                structured = np.fromfile(f, dtype=dtype_list)

                # Extract points (always x, y, z)
                points = np.column_stack([
                    structured["x"].astype(np.float32),
                    structured["y"].astype(np.float32),
                    structured["z"].astype(np.float32)
                ])

                # Extract labels if present
                labels = None
                dt_names = structured.dtype.names
                if dt_names is not None and "label" in dt_names:
                    labels = structured["label"].astype(np.uint8)

                return points, labels, metadata

        except Exception as e:
            cloudlog.debug(f"PCD read failed for {path}: {e}")
            return None


class StorageManager:
    """Enforce storage limits with automatic cleanup."""

    def __init__(self, root: Path, max_gb: float):
        self._root = root
        self._max_bytes = int(max_gb * 1024 ** 3)
        self._last_cleanup = 0
        self._cleanup_interval = 60  # seconds

    def used_mb(self) -> float:
        try:
            total = sum(
                f.stat().st_size
                for f in self._root.rglob("*")
                if f.is_file()
            )
            return total / (1024 * 1024)
        except Exception:
            return 0.0

    def cleanup_if_needed(self) -> bool:
        """Remove oldest sessions if over storage limit."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return False

        self._last_cleanup = now

        try:
            current_bytes = sum(
                f.stat().st_size
                for f in self._root.rglob("*")
                if f.is_file()
            )

            if current_bytes <= self._max_bytes * 0.9:  # 90% threshold
                return False

            # Get session directories sorted by age
            sessions = sorted(
                [d for d in self._root.iterdir() if d.is_dir()],
                key=lambda x: x.stat().st_mtime
            )

            freed_mb = 0
            for session in sessions[:-1]:  # Keep newest
                try:
                    session_size = sum(
                        f.stat().st_size for f in session.rglob("*") if f.is_file()
                    )
                    import shutil
                    shutil.rmtree(session)
                    freed_mb += session_size / (1024 * 1024)

                    if current_bytes - freed_mb * 1024 * 1024 <= self._max_bytes * 0.8:
                        break
                except Exception as e:
                    cloudlog.warning(f"Failed to remove session {session}: {e}")

            if freed_mb > 0:
                cloudlog.info(f"Storage cleanup freed {freed_mb:.1f} MB")
            return freed_mb > 0

        except Exception as e:
            cloudlog.error(f"Storage cleanup failed: {e}")
            return False


class GPUReprojector:
    """GPU-accelerated 3D reprojection using HAL.

    Converts stereo disparity + Q reprojection matrix to the
    depth + normalized-coordinate form consumed by the OpenCL
    `reproject` kernel, then dispatches to the GPU backend.
    """

    def __init__(self, client: InferenceClient):
        self.client = client
        self._backend = None
        self._initialized = False
        self._init_lock = threading.Lock()
        # Cached intrinsics derived from Q (recomputed if Q or shape changes)
        self._grid_shape: tuple[int, int | None] = None
        self._Q_sig: bytes | None = None
        self._x_norm: np.ndarray | None = None
        self._y_norm: np.ndarray | None = None
        self._focal: float = 0.0
        self._baseline: float = 0.0

    def initialize(self) -> bool:
        """Lazy initialization of GPU backend via HAL."""
        if self._initialized:
            return self._backend is not None

        with self._init_lock:
            if self._initialized:
                return self._backend is not None

            try:
                self._backend = self.client.acl()
                cloudlog.info("GPU reprojector initialized via HAL")
                self._initialized = True
                return True
            except RuntimeError as e:
                cloudlog.warning(f"GPU not available via HAL: {e}")
                self._backend = None
                self._initialized = True
                return False

    def _update_intrinsics(self, shape: tuple[int, int], Q: np.ndarray) -> bool:
        """(Re)compute x_norm / y_norm grids and extract f, baseline from Q."""
        q_sig = Q.tobytes()
        if self._grid_shape == shape and self._Q_sig == q_sig:
            return True

        # Q = [[1,0,0,-cx],[0,1,0,-cy],[0,0,0,f],[0,0,-1/b,0]]
        cx = -float(Q[0, 3])
        cy = -float(Q[1, 3])
        f = float(Q[2, 3])
        denom = float(Q[3, 2])
        if f <= 0.0 or denom == 0.0:
            return False
        baseline = -1.0 / denom

        h, w = shape
        u, v = np.meshgrid(
            np.arange(w, dtype=np.float32),
            np.arange(h, dtype=np.float32),
        )
        self._x_norm = ((u - cx) / f).astype(np.float32)
        self._y_norm = ((v - cy) / f).astype(np.float32)
        self._focal = f
        self._baseline = baseline
        self._grid_shape = shape
        self._Q_sig = q_sig
        return True

    def reproject(
        self,
        disparity: np.ndarray,
        Q: np.ndarray
    ) -> np.ndarray | None:
        """GPU-accelerated reprojection via HAL."""
        if not self.initialize() or self._backend is None:
            return None

        try:
            h, w = disparity.shape
            if not self._update_intrinsics((h, w), Q):
                return None

            # disparity -> depth (meters). Invalid disparities become 0.
            d = disparity.astype(np.float32)
            depth = np.zeros_like(d)
            valid = d > 0.1
            depth[valid] = self._focal * self._baseline / d[valid]

            result = self._backend.infer('reproject', {
                'depth': depth,
                'x_norm': self._x_norm,
                'y_norm': self._y_norm,
            })

            if not result.success:
                return None
            return result.outputs.get('xyz')

        except Exception as e:
            cloudlog.debug(f"GPU reprojection failed: {e}")
            return None


class Reconstructor3D:
    """3D reconstruction with hardware acceleration support via HAL."""

    def __init__(self, client: InferenceClient, use_gpu: bool = True):
        self.client = client
        self.Q = None
        self._load_calibration()

        # Hardware acceleration via HAL
        self._gpu_reprojector = GPUReprojector(client) if use_gpu else None
        self._use_gpu = use_gpu

        # Semantic components
        self.semantic_fusion: SemanticFusion | None = None
        self.yolo_3d: YOLO3DEstimator | None = None

        if self.Q is not None:
            f_px = float(self.Q[2, 2])
            self.semantic_fusion = SemanticFusion(
                image_width=640,
                image_height=480,
                focal_length_px=f_px
            )
            self.yolo_3d = YOLO3DEstimator(
                focal_length_px=f_px,
                image_width=640,
                image_height=480
            )

    def _load_calibration(self):
        """Load stereo calibration with fallback (factory intrinsics from HAL)."""
        Q = None
        try:
            from hal.drivers.camera import load_stereo_intrinsics
            cal = load_stereo_intrinsics()  # canonical path + legacy migration fallback
            if cal is not None:
                Q = cal.Q
        except ImportError:
            if CALIBRATION_PATH.exists():
                try:
                    Q = np.load(CALIBRATION_PATH)['Q']
                except Exception as e:
                    cloudlog.warning(f"Calibration load failed: {e}")
        if Q is not None:
            self.Q = Q
            cloudlog.info(f"Loaded calibration, baseline={-1.0/self.Q[3,2]:.3f}m")
            return
        self._init_default_calibration()

    def _init_default_calibration(self):
        """Default calibration for ExoPilot."""
        f_px = 700.0
        cx, cy = 320.0, 240.0
        baseline_m = 0.08  # 80mm default

        self.Q = np.array([
            [1, 0, 0, -cx],
            [0, 1, 0, -cy],
            [0, 0, 0, f_px],
            [0, 0, -1.0 / baseline_m, 0],
        ], dtype=np.float64)
        cloudlog.info(f"Using default calibration, baseline={baseline_m*1000:.0f}mm")

    def reconstruct(
        self,
        disparity: np.ndarray,
        confidence: np.ndarray | None,
        seg_mask: np.ndarray | None,
        yolo_dets: list[dict | None]
    ) -> tuple[np.ndarray | None, np.ndarray | None, dict[str, Any]]:
        """
        3D reconstruction pipeline with timing stats.

        Returns:
            (points_vehicle, labels, stats_dict)
        """
        stats = {
            "recon_method": "unknown",
            "recon_time_ms": 0.0,
            "num_points_raw": 0,
        }

        if self.Q is None:
            return None, None, stats

        t0 = time.perf_counter()

        # 1. 3D Reprojection (NumPy CPU — GPU kernel not yet implemented)
        xyz = self._gpu_reprojector.reproject(disparity, self.Q) if self._gpu_reprojector else None
        if xyz is not None:
            stats["recon_method"] = "gpu_opencl"
        else:
            xyz = cv2.reprojectImageTo3D(disparity, self.Q)
            stats["recon_method"] = "cpu_opencv"

        # 2. Filter by confidence and range
        if confidence is not None:
            valid = (confidence > 0.3) & np.isfinite(xyz).all(axis=2)
        else:
            valid = (disparity > 0) & np.isfinite(xyz).all(axis=2)

        z = xyz[:, :, 2]
        valid &= (z > 0.5) & (z < 100.0)

        points_camera = xyz[valid]
        stats["num_points_raw"] = len(points_camera)

        if len(points_camera) < 100:
            return None, None, stats

        t1 = time.perf_counter()
        stats["recon_time_ms"] = (t1 - t0) * 1000

        # 3. Semantic fusion
        labels = None
        if seg_mask is not None and self.semantic_fusion is not None:
            try:
                semantic_pc = self.semantic_fusion.fuse(points_camera, seg_mask)
                labels = semantic_pc.labels
            except Exception as e:
                cloudlog.debug(f"Semantic fusion failed: {e}")

        # 4. Transform to vehicle frame
        points_vehicle = self._camera_to_vehicle(points_camera)

        return points_vehicle, labels, stats

    def _camera_to_vehicle(self, points: np.ndarray) -> np.ndarray:
        """Transform from camera to vehicle frame."""
        vx = points[:, 2]   # forward
        vy = -points[:, 0]  # left
        vz = -points[:, 1]  # up
        return np.column_stack([vx, vy, vz]).astype(np.float32)


class PointcloudD:
    """
    3D reconstruction daemon (NON-CRITICAL).

    Reconstructs 3D point clouds from 2D stereo outputs.
    Failure does NOT affect core driving (stereod→gridd continues).
    """

    def __init__(self):
        set_daemon_affinity("pointcloudd")

        # Create HAL client - ONLY way to access hardware
        self.client = InferenceClient("pointcloudd")

        self.params = Params()
        self.enabled = self.params.get_bool("EOPPointcloudEnabled")
        self.save_to_disk = self.enabled
        self.enable_semantic = self.params.get_bool("EOPGridEnabled")

        rate_hz = int(self.params.get("EOPPointcloudRateHz") or DEFAULT_RATE_HZ)
        self.rate_hz = max(1, min(rate_hz, 20))

        # Subscribe to 2D outputs from stereod + position for geo-tagging
        self.sm = messaging.SubMaster(
            ["stereoDepth", "stereoSegments", "stereoDetections",
             "fusedPosition", "gpsLocationExternal"],
            poll="stereoDepth",
        )

        # Cached geo-position (updated whenever fusedPosition or GPS arrives)
        self._geo_lat: float = 0.0
        self._geo_lon: float = 0.0
        self._geo_alt: float = 0.0
        self._geo_heading: float = 0.0
        self._geo_accuracy: float = 0.0
        self._geo_source: str = "none"
        self._geo_valid: bool = False

        # Publish to surfaced + status
        self.pm = messaging.PubMaster([
            "pointcloudProcessed",
            "pointcloudStatus",
        ])

        self.rk = Ratekeeper(self.rate_hz, print_delay_threshold=None)

        # 3D reconstruction with GPU support via HAL
        use_gpu = self.params.get_bool("EOPPointcloudUseGPU")
        self.reconstructor = Reconstructor3D(self.client, use_gpu=use_gpu)

        # Semantic pipeline
        self.semantic_filter = SemanticPointFilter(FilterConfig(
            remove_noise=True,
            remove_dynamic=True,
            remove_unlabeled=True,
            max_point_distance=80.0
        ))
        self.voxel_filter = AdaptiveVoxelFilter()
        self.feature_extractor = SemanticFeatureExtractor()

        # Output
        self.pcd_writer = PcdWriter()
        self.storage = StorageManager(POINTCLOUD_ROOT, DEFAULT_MAX_GB)

        if self.save_to_disk:
            session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.session_dir = POINTCLOUD_ROOT / session_ts
            self.session_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.session_dir = None

        self.frame_count = 0
        self.stats = ReconstructionStats()

        # Check GPU/RGA availability via HAL
        gpu_available = False
        rga_available = False
        try:
            self.client.acl()
            gpu_available = True
        except RuntimeError:
            pass
        try:
            self.client.rga()
            rga_available = True
        except RuntimeError:
            pass

        self.stats.gpu_accelerated = use_gpu and gpu_available
        self.stats.rga_accelerated = rga_available

        cloudlog.info(
            f"PointcloudD: enabled={self.enabled}, rate={self.rate_hz}Hz, " +
            f"gpu={self.stats.gpu_accelerated}, rga={self.stats.rga_accelerated}"
        )

    def _decode_disparity(self, msg) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Decode disparity and confidence from message."""
        if not msg.disparityMap:
            return None, None

        try:
            h, w = msg.height, msg.width
            disparity = np.frombuffer(msg.disparityMap, dtype=np.float32).reshape((h, w))

            confidence = None
            if msg.confidenceMap:
                confidence = np.frombuffer(msg.confidenceMap, dtype=np.float32).reshape((h, w))

            return disparity, confidence
        except Exception as e:
            cloudlog.debug(f"Failed to decode disparity: {e}")
            self.stats.add_error(f"decode_disparity: {e}")
            return None, None

    def _decode_segmentation(self, msg) -> np.ndarray | None:
        """Decode PP-LiteSeg mask from message."""
        if not msg.stereoRightCamera:
            return None

        try:
            w, h = msg.rightWidth, msg.rightHeight
            return np.frombuffer(msg.stereoRightCamera, dtype=np.uint8).reshape((h, w))
        except Exception as e:
            cloudlog.debug(f"Failed to decode segmentation: {e}")
            return None

    def _decode_yolo(self, msg) -> list[dict]:
        """Decode YOLO detections from message."""
        dets = []
        try:
            for det in msg.detections:
                dets.append({
                    'class': det.className,
                    'confidence': det.confidence,
                    'bbox': list(det.bbox2d) if hasattr(det, 'bbox2d') else [0, 0, 0, 0],
                })
        except Exception as e:
            cloudlog.debug(f"Failed to decode YOLO: {e}")
        return dets

    def _process_semantic(
        self,
        points: np.ndarray,
        labels: np.ndarray | None
    ) -> tuple[np.ndarray, np.ndarray | None, dict[str, Any]]:
        """Apply semantic filtering and adaptive downsampling."""
        t0 = time.perf_counter()

        if labels is None or not self.enable_semantic:
            return points, None, {"mode": "no_semantics"}

        try:
            # 1. Semantic filtering
            filtered_points, filtered_labels, filter_stats = self.semantic_filter.filter(
                points, labels
            )

            # 2. Adaptive voxel downsampling
            downsampled_points, downsampled_labels = self.voxel_filter.downsample(
                filtered_points, filtered_labels
            )

            filter_time_ms = (time.perf_counter() - t0) * 1000

            return downsampled_points, downsampled_labels, {
                "mode": "semantic",
                "filter_time_ms": filter_time_ms,
                "original_count": filter_stats.get("original_count", 0),
                "filtered_count": len(filtered_points),
                "downsampled_count": len(downsampled_points),
                "removal_rate": filter_stats.get("removal_rate", 0),
            }
        except Exception as e:
            cloudlog.warning(f"Semantic processing failed: {e}")
            self.stats.add_error(f"semantic_process: {e}")
            return points, labels, {"mode": "error", "error": str(e)}

    def _update_geo_position(self) -> None:
        """Update cached geo-position from fusedPosition (preferred) or gpsLocationExternal."""
        # Prefer coordinationd fusedPosition — road-constrained, SGM/OSM corrected
        if self.sm.updated["fusedPosition"]:
            fused = self.sm["fusedPosition"]
            if fused.confidence > 0.2:
                self._geo_lat = fused.positionGeodetic.latitude
                self._geo_lon = fused.positionGeodetic.longitude
                self._geo_alt = fused.positionGeodetic.altitude
                self._geo_accuracy = fused.positionECEF.xStd  # horizontal std as proxy
                self._geo_source = "fused"
                self._geo_valid = True
                return

        # Fallback: raw GPS (cold-start or coordinationd not running)
        if self.sm.updated["gpsLocationExternal"]:
            gps = self.sm["gpsLocationExternal"]
            if gps.accuracy < 30.0:
                self._geo_lat = gps.latitude
                self._geo_lon = gps.longitude
                self._geo_alt = gps.altitude
                self._geo_accuracy = gps.accuracy
                self._geo_source = "gps"
                self._geo_valid = True

    def _extract_features(self, points: np.ndarray, labels: np.ndarray) -> dict[str, Any]:
        """Extract semantic landmarks."""
        try:
            landmarks = self.feature_extractor.extract(points, labels)
            return {
                "num_poles": len(landmarks.poles),
                "num_signs": len(landmarks.signs),
                "num_corners": len(landmarks.building_corners),
                "num_curbs": len(landmarks.curb_lines),
            }
        except Exception as e:
            cloudlog.debug(f"Feature extraction failed: {e}")
            return {}

    def _publish_processed(
        self,
        timestamp: int,
        points: np.ndarray,
        labels: np.ndarray | None,
        features: dict[str, Any],
        stats: dict[str, Any]
    ):
        """Publish processed point cloud to surfaced."""
        try:
            msg = messaging.new_message("pointcloudProcessed")
            pc = msg.pointcloudProcessed

            pc.timestamp = timestamp
            pc.frameId = self.frame_count

            # Limit points for message size
            max_points = 10000
            if len(points) > max_points:
                indices = np.random.choice(len(points), max_points, replace=False)
                points = points[indices]
                if labels is not None:
                    labels = labels[indices]

            # Pack points
            num_points = len(points)
            pc_points = pc.init("points", num_points)

            for i, p in enumerate(points):
                pc_points[i].x = float(p[0])
                pc_points[i].y = float(p[1])
                pc_points[i].z = float(p[2])

            # Pack labels if available
            if labels is not None and len(labels) == num_points:
                pc_labels = pc.init("pointLabels", num_points)
                for i, label in enumerate(labels):
                    pc_labels[i] = int(label)

            pc.validPoints = num_points
            pc.wasFiltered = stats.get("removal_rate", 0) > 0
            pc.source = "pointcloudd_3d"

            # Geo-tagging: consumers (coordinationd SGM module) use these for map registration
            pc.hasGeoPosition = self._geo_valid
            pc.geoLat = self._geo_lat
            pc.geoLon = self._geo_lon
            pc.geoAlt = self._geo_alt
            pc.geoHeading = self._geo_heading
            pc.geoAccuracy = self._geo_accuracy
            pc.geoSource = self._geo_source

            self.pm.send("pointcloudProcessed", msg)

        except Exception as e:
            cloudlog.error(f"Failed to publish processed: {e}")
            self.stats.add_error(f"publish: {e}")

    def _save_to_disk(
        self,
        points: np.ndarray,
        labels: np.ndarray | None,
        features: dict[str, Any],
        timestamp: int
    ):
        """Save PCD file with semantic labels."""
        if not self.save_to_disk or self.session_dir is None:
            return

        try:
            pcd_path = self.session_dir / f"frame_{self.frame_count:06d}.pcd"
            success = self.pcd_writer.write(
                pcd_path, points, labels, features,
                self.frame_count, timestamp,
                geo_lat=self._geo_lat,
                geo_lon=self._geo_lon,
                geo_alt=self._geo_alt,
                geo_heading=self._geo_heading,
                geo_source=self._geo_source
            )
            if success:
                self.stats.frames_processed += 1
        except Exception as e:
            cloudlog.error(f"Save error: {e}")
            self.stats.add_error(f"save: {e}")

    def _publish_status(self, timestamp: int, num_points: int, proc_stats: dict[str, Any]):
        """Publish detailed status."""
        try:
            msg = messaging.new_message("pointcloudStatus")
            ps = msg.pointcloudStatus

            ps.timestamp = timestamp
            ps.enabled = self.enabled
            ps.frameId = self.frame_count
            ps.savedFrames = self.pcd_writer.frames_written
            ps.storageUsedMb = self.storage.used_mb()
            ps.validPoints = num_points
            ps.isFiltered = proc_stats.get("removal_rate", 0) > 0

            self.pm.send("pointcloudStatus", msg)

        except Exception as e:
            cloudlog.debug(f"Status publish failed: {e}")

    def _cleanup_storage(self):
        """Periodic storage cleanup."""
        if self.frame_count % 100 == 0:  # Every 100 frames
            self.storage.cleanup_if_needed()

    def run(self):
        """Main loop - 3D reconstruction with robust error handling."""
        if not self.enabled:
            cloudlog.info("PointcloudD disabled")
            return

        cloudlog.info("PointcloudD running (3D reconstruction)")

        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 10

        while True:
            loop_start = time.perf_counter()

            try:
                self.sm.update(0)
                int(time.monotonic() * 1e9)

                # Update cached geo-position from fusedPosition / GPS on every tick
                self._update_geo_position()

                if self.sm.updated["stereoDepth"]:
                    sd = self.sm["stereoDepth"]

                    # Decode 2D inputs
                    disparity, confidence = self._decode_disparity(sd)

                    if disparity is not None:
                        # Get segmentation mask
                        seg_mask = None
                        if self.sm.updated["stereoSegments"]:
                            seg_msg = self.sm["stereoSegments"]
                            seg_mask = self._decode_segmentation(seg_msg)

                        # Get YOLO detections
                        yolo_dets = []
                        if self.sm.updated["stereoDetections"]:
                            det_msg = self.sm["stereoDetections"]
                            yolo_dets = self._decode_yolo(det_msg)

                        # 3D reconstruction
                        points, labels, recon_stats = self.reconstructor.reconstruct(
                            disparity, confidence, seg_mask, yolo_dets
                        )

                        if points is not None and len(points) > 0:
                            # Semantic filtering + downsampling
                            t_filter_start = time.perf_counter()
                            points_proc, labels_proc, filter_stats = self._process_semantic(
                                points, labels
                            )
                            filter_time_ms = (time.perf_counter() - t_filter_start) * 1000

                            # Feature extraction
                            features = {}
                            if labels_proc is not None:
                                features = self._extract_features(points_proc, labels_proc)

                            # Merge stats
                            total_time_ms = (time.perf_counter() - loop_start) * 1000
                            self.stats.update_timing(
                                recon_stats.get("recon_time_ms", 0),
                                filter_time_ms,
                                total_time_ms
                            )

                            # Publish to surfaced
                            self._publish_processed(
                                sd.timestamp, points_proc, labels_proc,
                                features, filter_stats
                            )

                            # Save to disk
                            if self.save_to_disk:
                                self._save_to_disk(
                                    points_proc, labels_proc, features, sd.timestamp
                                )

                            # Publish status
                            self._publish_status(sd.timestamp, len(points_proc), filter_stats)

                            consecutive_errors = 0
                        else:
                            self.stats.frames_dropped += 1
                    else:
                        self.stats.frames_dropped += 1

                    self.frame_count += 1
                    self._cleanup_storage()

                self.rk.keep_time()

            except Exception as e:
                consecutive_errors += 1
                self.stats.add_error(f"main_loop: {e}")
                cloudlog.error(f"Main loop error ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}")

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    cloudlog.critical("Too many consecutive errors, restarting...")
                    # Let manager restart us
                    raise

                time.sleep(0.1)  # Brief pause before retry


def main() -> int:
    import logging
    logging.basicConfig(level=logging.INFO)
    daemon = None
    try:
        daemon = PointcloudD()
        daemon.run()
        return 0
    except KeyboardInterrupt:
        cloudlog.info("PointcloudD stopped")
        return 0
    except Exception as e:
        cloudlog.exception(f"PointcloudD fatal error: {e}")
        return 1
    finally:
        if daemon is not None and hasattr(daemon, 'client'):
            daemon.client.release()


if __name__ == "__main__":
    exit(main())
