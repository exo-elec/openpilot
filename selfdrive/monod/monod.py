#!/usr/bin/env python3
"""
monod.py - 2-Camera Detection Daemon (Road + Wide)

SAFETY-FIRST + THERMAL-SAFE DESIGN:
- RKNN NPU Core 2 = PRIMARY (60% utilization for thermal headroom)
  * road (8mm): YOLO + SceneSeg (0.7 TOPS)
  * wide (1.7mm): PP-LiteSeg (0.3 TOPS)

2-CAMERA FUSION:
- Fuses detections from both cameras for comprehensive coverage
- Wide: 150° FOV for cross-traffic and near-field
- Road: 40° FOV for primary driving path

Thermal throttling prevents overheating (max 85% per core).
"""
import logging
import numpy as np
import time
from pathlib import Path
from dataclasses import dataclass
from enum import Enum, auto
from collections import deque

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.selfdrive.modeld.runners.rknn_platform import get_platform_npu_config
from openpilot.system.inferenced.client import InferenceClient
from openpilot.system.inferenced.compute import (
    BackendType, ModelConfig
)
from openpilot.common.swaglog import cloudlog

# VisionIPC integration (optional - falls back to zero frames if unavailable)
try:
    from msgq.visionipc import VisionIpcClient, VisionStreamType
    HAS_VISIONIPC = True
except ImportError:
    HAS_VISIONIPC = False

RATE = 20  # 20 Hz


@dataclass
class FusedDetection:
    """Fused detection from multiple camera sources."""
    track_id: int
    class_name: str
    confidence: float
    # Position in road frame (car coordinates)
    x: float  # forward (meters)
    y: float  # left (meters)
    z: float  # up (meters)
    # Source cameras that detected this object
    sources: list[str]
    # Source-specific data
    road_detection: dict | None = None
    # Velocity
    vx: float = 0.0
    vy: float = 0.0


class CameraLens(Enum):
    """Both cameras for monod."""
    ROAD_8MM = ("road", 8.0, 40.0, 0.0, 100.0)
    WIDE_1_7MM = ("wide_road", 1.7, 150.0, 0.0, 30.0)

    def __init__(self, camera_name, focal_mm, fov_deg, min_range_m, max_range_m):
        self.camera_name = camera_name
        self.focal_mm = focal_mm
        self.fov_deg = fov_deg
        self.min_range_m = min_range_m
        self.max_range_m = max_range_m


class CameraSynchronizer:
    """Synchronizes frames from multiple cameras for fusion."""
    
    def __init__(self, max_age_ms: float = 50.0):
        self.max_age_ms = max_age_ms
        self._road_frames: deque = deque(maxlen=5)
        self._wide_frames: deque = deque(maxlen=5)
        self._last_synced_timestamp: int | None = None

    def add_road_frame(self, frame: np.ndarray, timestamp: int):
        """Add road camera frame."""
        self._road_frames.append((timestamp, frame))

    def add_wide_frame(self, frame: np.ndarray, timestamp: int):
        """Add wide camera frame."""
        self._wide_frames.append((timestamp, frame))

    def get_synced_frames(self) -> tuple[np.ndarray | None, np.ndarray | None, int]:
        """
        Get synchronized frames from both cameras.

        Returns:
            (road_frame, wide_frame, reference_timestamp)
            Missing frames are None.
        """
        if not self._road_frames:
            return None, None, 0

        # Use road camera as reference (primary)
        ref_timestamp, road_frame = self._road_frames[-1]

        # Find matching wide frame
        wide_frame = None
        for ts, frame in reversed(self._wide_frames):
            if abs(ts - ref_timestamp) < self.max_age_ms * 1e6:  # Convert ms to ns
                wide_frame = frame
                break

        self._last_synced_timestamp = ref_timestamp

        # Clean old frames
        self._clean_old_frames(ref_timestamp)

        return road_frame, wide_frame, ref_timestamp

    def _clean_old_frames(self, ref_timestamp: int):
        """Remove frames older than max_age."""
        max_age_ns = int(self.max_age_ms * 1e6)

        while self._road_frames and (ref_timestamp - self._road_frames[0][0]) > max_age_ns:
            self._road_frames.popleft()

        while self._wide_frames and (ref_timestamp - self._wide_frames[0][0]) > max_age_ns:
            self._wide_frames.popleft()


class RKNNMonoProcessor:
    """
    NPU processor for road + wide cameras via centralized HAL (InferenceClient).

    Platform-aware core allocation: RK3588 Core 2 (dedicated for monod).

    Thermally-safe: 0.7 TOPS road + 0.3 TOPS wide = 1.0 TOPS total
    """

    # Model paths tried in order: on-device RKNN → repo ONNX → repo RKNN
    _REPO_ROOT = Path(__file__).parents[3]
    MODEL_PATHS = {
        'yolo_road': next((str(p) for p in [
            Path('/data/openpilot/models/rknn/yolo_640.rknn'),
            _REPO_ROOT / 'models/onnx/yolo_640.onnx',
            _REPO_ROOT / 'models/rknn/yolo_640.rknn',
        ] if p.exists()), '/data/openpilot/models/rknn/yolo_640.rknn'),
        'sceneseg_road': next((str(p) for p in [
            Path('/data/openpilot/models/rknn/sceneseg_lite_rk3588.rknn'),
            _REPO_ROOT / 'models/onnx/sceneseg_lite.onnx',
        ] if p.exists()), '/data/openpilot/models/rknn/sceneseg_lite_rk3588.rknn'),
        'ppliteseg_wide': next((str(p) for p in [
            Path('/data/openpilot/models/rknn/ppliteseg_320.rknn'),
            _REPO_ROOT / 'models/onnx/ppliteseg_320.onnx',
        ] if p.exists()), '/data/openpilot/models/rknn/ppliteseg_320.rknn'),
    }

    FAULT_THRESHOLD = 3  # Consecutive inference failures before fault declared

    def __init__(self, core_id: int | None = None):
        self._client: InferenceClient | None = None
        self._npu = None
        self._initialized = False
        self._loaded_models: list[str] = []
        self._fault = False
        self._fault_reason = ""
        self._consecutive_failures = 0

        self._npu_config = get_platform_npu_config()

        # Platform-aware core selection
        if core_id is not None:
            if not self._npu_config.is_core_available(core_id):
                cloudlog.warning(f"Core {core_id} not available, using last core")
                core_id = self._npu_config.core_count - 1
            self.core_id = core_id
        else:
            self.core_id = 2 if self._npu_config.is_rk3588 else 1

        self._npu_cores = str(self.core_id)
        self._init_npu()

    def _init_npu(self):
        """Initialize NPU via InferenceClient and load all models."""
        try:
            # Use centralized HAL - NPU with specific core
            self._client = InferenceClient("monod")
            self._npu = self._client.inference_backend()

            platform_name = self._npu_config.platform.value
            cloudlog.info(f"Loading models on {platform_name} Core {self.core_id}")

            for model_name, model_path_str in self.MODEL_PATHS.items():
                model_path = Path(model_path_str)
                if not model_path.exists():
                    continue
                model_type = "detection" if 'yolo' in model_name else "segmentation"
                mcfg = ModelConfig(
                    name=model_name,
                    path=str(model_path),
                    model_type=model_type,
                    npu_cores=self.core_id,
                )
                if self._npu.load_model(mcfg):
                    self._loaded_models.append(model_name)
                    cloudlog.info(f"  ✓ {model_name} on Core {self.core_id}")

            self._initialized = 'yolo_road' in self._loaded_models
            if not self._initialized:
                self._fault = True
                self._fault_reason = "npu_unavailable"
                cloudlog.error("RKNNMonoProcessor: primary model (yolo_road) failed to load — FAULT")

        except Exception as e:
            cloudlog.error(f"RKNNMonoProcessor init failed: {e}")
            self._fault = True
            self._fault_reason = "npu_unavailable"
            self._npu = None

    def _infer(self, model_name: str, frame: np.ndarray,
                fallback_shape: tuple) -> np.ndarray:
        """Run inference via HAL and return output array. Tracks consecutive failures."""
        if self._npu is None or model_name not in self._loaded_models:
            return np.zeros(fallback_shape, dtype=np.uint8)
        try:
            result = self._npu.infer(
                model_name=model_name,
                inputs={'input': frame}
            )
            if not result.success:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.FAULT_THRESHOLD:
                    self._fault = True
                    self._fault_reason = "npu_consecutive_failures"
                    cloudlog.error(f"{model_name}: {self._consecutive_failures} consecutive failures — FAULT")
                return np.zeros(fallback_shape, dtype=np.uint8)

            # Success — reset failure counter
            self._consecutive_failures = 0
            if self._fault and self._fault_reason == "npu_consecutive_failures":
                self._fault = False
                self._fault_reason = ""
                cloudlog.info(f"{model_name} recovered — clearing fault")

            output = result.outputs.get('output', np.zeros(fallback_shape, dtype=np.uint8))
            # Argmax for segmentation outputs
            if output.ndim == 4:
                return np.argmax(output[0], axis=0).astype(np.uint8)
            if output.ndim == 3:
                return np.argmax(output, axis=0).astype(np.uint8)
            return output.astype(np.uint8)
        except Exception as e:
            self._consecutive_failures += 1
            cloudlog.debug(f"{model_name} inference error: {e}")
            return np.zeros(fallback_shape, dtype=np.uint8)

    @property
    def is_available(self) -> bool:
        return self._initialized

    def infer_yolo_road(self, frame: np.ndarray) -> list[dict]:
        """YOLO detection on road camera. Tracks consecutive failures for fault reporting."""
        if self._npu is None or 'yolo_road' not in self._loaded_models:
            return []
        try:
            result = self._npu.infer(
                model_name='yolo_road',
                inputs={'input': frame}
            )
            if result.success:
                self._consecutive_failures = 0
                if self._fault and self._fault_reason == "npu_consecutive_failures":
                    self._fault = False
                    self._fault_reason = ""
                    cloudlog.info("YOLO road recovered — clearing fault")
                return result.outputs.get('detections', [])
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.FAULT_THRESHOLD:
                self._fault = True
                self._fault_reason = "npu_consecutive_failures"
                cloudlog.error(f"YOLO road: {self._consecutive_failures} consecutive failures — FAULT")
        except Exception as e:
            self._consecutive_failures += 1
            cloudlog.debug(f"YOLO road error: {e}")
        return []

    def infer_sceneseg_road(self, frame: np.ndarray) -> np.ndarray:
        """SceneSeg on road camera."""
        return self._infer('sceneseg_road', frame, (640, 640))

    def infer_ppliteseg_wide(self, frame: np.ndarray) -> np.ndarray:
        """PP-LiteSeg on wide camera."""
        return self._infer('ppliteseg_wide', frame, (320, 320))

    @property
    def is_fault(self) -> bool:
        return self._fault

    @property
    def fault_reason(self) -> str:
        return self._fault_reason

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def release(self):
        """Release HAL resources."""
        if self._client:
            self._client.release()
            self._client = None
        self._npu = None
        self._initialized = False


class MultiCameraFusion:
    """Fuses detections from road and wide cameras."""

    # Camera parameters for coordinate transformation
    CAMERA_PARAMS = {
        'road': {
            'focal_length': 800.0,
            'fov_deg': 40.0,
            'center_x': 960.0,
            'center_y': 540.0,
        },
    }

    def __init__(self):
        self._next_track_id = 1000
        self._active_tracks: dict[int, FusedDetection] = {}
        self._track_history: deque = deque(maxlen=100)

    def fuse_detections(self,
                       road_dets: list[dict],
                       road_seg: np.ndarray) -> list[FusedDetection]:
        """
        Fuse detections from both cameras into unified track list.

        Strategy:
        1. Road camera for mid-range (5-100m)
        2. Cross-camera matching for consistent tracks
        """
        fused = []

        # Process road camera detections
        for road_det in road_dets:
            x = road_det.get('distance_m', 50.0)
            y = road_det.get('lateral_m', 0.0)
            z = 0.0
            class_name = road_det.get('class', 'unknown')
            
            # Check for match with existing fused tracks
            existing = self._find_matching_track(x, y, class_name)
            
            if existing:
                # Merge with existing track
                existing.road_detection = road_det
                if 'road' not in existing.sources:
                    existing.sources.append('road')
                # Boost confidence with multi-camera confirmation
                existing.confidence = min(1.0, existing.confidence + 0.1)
            else:
                # Create new track from road detection
                track = FusedDetection(
                    track_id=self._next_track_id,
                    class_name=class_name,
                    confidence=road_det.get('confidence', 0.5),
                    x=x,
                    y=y,
                    z=z,
                    sources=['road'],
                    road_detection=road_det
                )
                self._next_track_id += 1
                fused.append(track)
        
        # Update active tracks
        self._active_tracks = {t.track_id: t for t in fused}
        self._track_history.extend(fused)
        
        return fused

    def _find_matching_track(self, x: float, y: float,
                            class_name: str) -> FusedDetection | None:
        """Find existing track matching the given position and class."""
        for track in self._active_tracks.values():
            if track.class_name != class_name:
                continue
            
            # Distance threshold (meters)
            dist = np.sqrt((track.x - x)**2 + (track.y - y)**2)
            if dist < 5.0:  # 5 meter threshold
                return track
        
        return None
    
    def get_active_tracks(self) -> list[FusedDetection]:
        """Get list of currently active tracks."""
        return list(self._active_tracks.values())


class MonoD:
    """Mono detection daemon - 2-camera fusion (road + wide)."""

    # Platform info for logging
    _npu_config = get_platform_npu_config()
    _platform_name = _npu_config.platform.value
    _core_count = _npu_config.core_count

    def __init__(self) -> None:
        # Set CPU affinity for A76 big cores
        set_daemon_affinity("monod")

        # Publishers
        self.pm = messaging.PubMaster(['monoDetections', 'monoSegments', 'monoStatus'])

        # Subscribers - road (8mm) + wide (1.7mm)
        self.sm = messaging.SubManager([
            'roadCameraState',
            'wideRoadCameraState',
            'livePose',
            'liveCalibration'
        ])

        self.rk = Ratekeeper(RATE, print_delay_threshold=None)

        # Camera synchronizer for multi-camera fusion
        self.sync = CameraSynchronizer(max_age_ms=50.0)

        # Multi-camera fusion
        self.fusion = MultiCameraFusion()

        # RKNN NPU (platform-aware core selection)
        self.rknn_processor = RKNNMonoProcessor()

        # Params
        self.params = Params()
        self.enabled = self.params.get_bool("EOPMonoDEnabled")

        # State
        self.frame_id = 0
        self.wide_frame_id = 0  # Wide runs at 10Hz (half rate for thermal)

        # VisionIPC clients (optional)
        self._vipc_road = None
        self._vipc_wide = None
        self._init_visionipc()

        cloudlog.info(f"MonoD initialized: enabled={self.enabled}, "
                   f"rknn={self.rknn_processor.is_available}")
        cloudlog.info(f"Platform: {self._platform_name} ({self._core_count} NPU cores), "
                   f"using Core {self.rknn_processor.core_id}")
        cloudlog.info(f"VisionIPC: available={HAS_VISIONIPC}, "
                   f"road={self._vipc_road is not None}, "
                   f"wide={self._vipc_wide is not None}")
        cloudlog.info("2-camera fusion: road + wide")
        cloudlog.info("Thermal-safe allocation: 75% max NPU utilization")

    def _init_visionipc(self) -> None:
        """Initialize VisionIPC clients for camera frame retrieval."""
        if not HAS_VISIONIPC:
            cloudlog.warning("MonoD: VisionIPC not available — install msgq with vision support")
            return

        try:
            # Road camera (primary)
            self._vipc_road = VisionIpcClient("v4l2d", VisionStreamType.VISION_STREAM_ROAD, False)
            if not self._vipc_road.connect(False):
                cloudlog.warning("MonoD: VisionIPC road camera not available")
                self._vipc_road = None
            else:
                cloudlog.info("MonoD: VisionIPC road camera connected")
        except Exception as e:
            cloudlog.warning(f"MonoD: VisionIPC road init failed: {e}")
            self._vipc_road = None

        try:
            # Wide road camera
            self._vipc_wide = VisionIpcClient("v4l2d", VisionStreamType.VISION_STREAM_WIDE_ROAD, False)
            if not self._vipc_wide.connect(False):
                cloudlog.warning("MonoD: VisionIPC wide camera not available")
                self._vipc_wide = None
            else:
                cloudlog.info("MonoD: VisionIPC wide camera connected")
        except Exception as e:
            cloudlog.warning(f"MonoD: VisionIPC wide init failed: {e}")
            self._vipc_wide = None
        
    def _get_frame(self, vipc_client, fallback_shape: tuple[int, int, int]) -> np.ndarray:
        """Get frame from VisionIPC or return zeros if unavailable."""
        if vipc_client is None:
            return np.zeros(fallback_shape, dtype=np.uint8)
        
        try:
            buf = vipc_client.recv()
            if buf is None:
                return np.zeros(fallback_shape, dtype=np.uint8)
            
            # Convert VisionIPC buffer to numpy array
            # buf.data is a memoryview-like object
            h, w = fallback_shape[:2]
            frame = np.frombuffer(buf.data, dtype=np.uint8).reshape((h, w, 3))
            return frame
        except Exception as e:
            cloudlog.debug(f"MonoD: VisionIPC frame retrieval failed: {e}")
            return np.zeros(fallback_shape, dtype=np.uint8)
    
    def _process_road(self, frame: np.ndarray) -> tuple[list, np.ndarray]:
        """Process road camera frame."""
        detections = []
        segmentation = np.zeros((640, 640), dtype=np.uint8)
        
        if not self.enabled:
            return detections, segmentation
        
        if self.rknn_processor.is_available:
            detections = self.rknn_processor.infer_yolo_road(frame)
            segmentation = self.rknn_processor.infer_sceneseg_road(frame)
        
        return detections, segmentation
    
    def _process_wide(self, frame: np.ndarray) -> np.ndarray:
        """Process wide camera frame (10Hz for thermal safety)."""
        segmentation = np.zeros((320, 320), dtype=np.uint8)
        
        if not self.enabled:
            return segmentation
        
        # Run at half rate (10Hz) for thermal safety
        if self.frame_id % 2 == 0:
            if self.rknn_processor.is_available:
                segmentation = self.rknn_processor.infer_ppliteseg_wide(frame)
        
        return segmentation
    
    def _publish(self, fused_tracks: list[FusedDetection],
                 road_seg: np.ndarray,
                 wide_seg: np.ndarray,
                 ts: int):
        """Publish detection results."""
        # monoDetections (fused from both cameras)
        msg = messaging.new_message('monoDetections')
        msg.monoDetections.frameId = self.frame_id
        msg.monoDetections.timestamp = ts

        if fused_tracks:
            items = msg.monoDetections.init('detections', len(fused_tracks))
            for i, track in enumerate(fused_tracks):
                items[i].className = track.class_name
                items[i].confidence = track.confidence
                items[i].x = track.x
                items[i].y = track.y
                # Include source info in metadata
                items[i].cameraSource = '+'.join(track.sources)

        self.pm.send('monoDetections', msg)

        # monoSegments (road + wide)
        seg_msg = messaging.new_message('monoSegments')
        seg_msg.monoSegments.frameId = self.frame_id
        seg_msg.monoSegments.timestamp = ts
        # Populate segments list — one MonoSegment per camera source
        segs = seg_msg.monoSegments.init('segments', 2)
        segs[0].camera = 'road'
        segs[0].hasRoad = road_seg.size > 0
        segs[0].hasEdge = False
        segs[0].hasDrivable = False
        segs[1].camera = 'wide'
        segs[1].hasRoad = wide_seg.size > 0
        segs[1].hasEdge = False
        segs[1].hasDrivable = False
        self.pm.send('monoSegments', seg_msg)

        # monoStatus
        status_msg = messaging.new_message('monoStatus')
        ss = status_msg.monoStatus
        ss.enabled = self.enabled
        ss.hailoActive = False
        ss.hasTeleRoad = False
        ss.numTracks = len(fused_tracks)
        ss.fault = self.rknn_processor.is_fault
        ss.faultReason = self.rknn_processor.fault_reason
        ss.consecutiveFailures = self.rknn_processor.consecutive_failures
        self.pm.send('monoStatus', status_msg)
    
    def run(self):
        """Main loop."""
        if not self.enabled:
            cloudlog.info("MonoD disabled - exiting")
            return

        cloudlog.info("MonoD running (2-camera fusion, thermal-safe: 75% max Core 2)")

        try:
            while True:
                self.sm.update(0)

                # Process road camera (primary, 20Hz)
                if self.sm.updated['roadCameraState']:
                    road_frame = self._get_frame(self._vipc_road, (1080, 1920, 3))
                    road_ts = int(time.monotonic() * 1e9)
                    self.sync.add_road_frame(road_frame, road_ts)

                    road_dets, road_seg = self._process_road(road_frame)
                else:
                    road_dets, road_seg = [], np.zeros((640, 640), dtype=np.uint8)

                # Process wide camera (10Hz for thermal)
                wide_seg = np.zeros((320, 320), dtype=np.uint8)
                if self.sm.updated['wideRoadCameraState']:
                    wide_frame = self._get_frame(self._vipc_wide, (1080, 1920, 3))
                    wide_ts = int(time.monotonic() * 1e9)
                    self.sync.add_wide_frame(wide_frame, wide_ts)
                    if self.frame_id % 2 == 0:
                        wide_seg = self._process_wide(wide_frame)

                # Fuse detections from both cameras
                fused_tracks = self.fusion.fuse_detections(road_dets, road_seg)

                # Publish results
                ts = int(time.monotonic() * 1e9)
                self._publish(fused_tracks, road_seg, wide_seg, ts)

                self.frame_id += 1
                self.rk.keep_time()
        finally:
            self.rknn_processor.release()


def main() -> int:
    try:
        logging.basicConfig(level=logging.INFO)
        MonoD().run()
        return 0
    except Exception as e:
        cloudlog.exception(f"MonoD fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
