#!/usr/bin/env python3
"""
StereoD — 2D Stereo Depth Daemon (CRITICAL for ADAS)

Uses centralized InferenceD HAL for all compute:
- GPU (ACL) for SGM stereo depth
- NPU (RKNN) for YOLO + PP-LiteSeg

No direct hardware access - all through HAL.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import cv2

import cereal.messaging as messaging
from msgq.visionipc import VisionStreamType, VisionIpcClient
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.system.hardware.camera_geometry import CameraGeometry
from openpilot.system.hardware.registry import PlatformRegistry
from openpilot.common.perf_monitor import PerformanceMonitor
from openpilot.common.swaglog import cloudlog

# Centralized HAL - ONLY way to access hardware
from openpilot.system.inferenced import InferenceClient

# SGM via HAL
from openpilot.selfdrive.stereod.sgm import SGM, SGMConfig, SGMError, SGMDeviceError, SGMTimeoutError

# Constants
RATE = 20  # Hz
STEREO_LEFT_STREAM = 4
STEREO_RIGHT_STREAM = 5
FAULT_THRESHOLD = 3
MAX_SGM_LATENCY_MS = 50.0

# Cityscapes class indices
CITYSCAPES_ROAD = [0, 1]
CITYSCAPES_FOREGROUND = [11, 12, 13, 14, 15, 16, 17, 18]


def _nv12_to_gray(data: bytes, width: int, height: int) -> np.ndarray:
    """Extract Y plane from NV12."""
    return np.frombuffer(data, dtype=np.uint8).reshape((height * 3 // 2, width))[:height]


def _nv12_to_bgr(data: bytes, width: int, height: int) -> np.ndarray:
    """Convert NV12 to BGR for NPU input."""
    nv12 = np.frombuffer(data, dtype=np.uint8).reshape((height * 3 // 2, width))
    return cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)


class StereoD:
    """
    2D stereo depth daemon using centralized HAL.

    All hardware access through InferenceClient:
    - GPU for SGM
    - NPU for YOLO/segmentation
    """

    def __init__(self):
        set_daemon_affinity("stereod")

        # Create HAL client - ONLY way to access hardware
        self.client = InferenceClient("stereod")

        self.params = Params()
        self.enabled = self.params.get_bool("EOPStereoEnabled")

        # Get hardware from HAL
        try:
            self.gpu = self.client.acl()
            cloudlog.info("StereoD: GPU backend acquired")
        except RuntimeError as e:
            cloudlog.error(f"StereoD: GPU not available: {e}")
            self.gpu = None

        try:
            self.npu = self.client.npu()
            cloudlog.info("StereoD: NPU backend acquired")
        except RuntimeError as e:
            cloudlog.warning(f"StereoD: NPU not available: {e}")
            self.npu = None

        # Camera geometry
        hardware = PlatformRegistry.create()
        self.geometry: CameraGeometry = hardware.get_camera_geometry()
        self.stereo_baseline_m = self.geometry.get_baseline('stereo_left', 'stereo_right')
        cloudlog.info(f"Stereo baseline: {self.stereo_baseline_m * 1000:.0f}mm")

        # SGM state
        self._sgm: SGM | None = None
        self._sgm_config: SGMConfig | None = None
        self._gpu_sgm_available = False

        # Fault tracking
        self._fault = False
        self._fault_reason = ""
        self._consecutive_failures = 0
        self._FAULT_THRESHOLD = FAULT_THRESHOLD

        # Performance monitoring
        self._metrics = PerformanceMonitor("stereod")
        self._metrics.set_target_fps("output", RATE)

        # Calibration state
        self.width = 0
        self.height = 0
        self.left_map1 = self.left_map2 = None
        self.right_map1 = self.right_map2 = None
        self.Q = None

        # VisionIPC
        self.vipc_left = VisionIpcClient("v4l2d", STEREO_LEFT_STREAM, True)
        self.vipc_right = VisionIpcClient("v4l2d", STEREO_RIGHT_STREAM, True)
        self.vipc_wide = VisionIpcClient("v4l2d", VisionStreamType.VISION_STREAM_WIDE_ROAD, False)

        # Publishers
        self.pm = messaging.PubMaster([
            'stereoDepth',
            'stereoDetections',
            'stereoSegments',
            'stereoStatus',
        ])
        self.rk = Ratekeeper(RATE, print_delay_threshold=None)

        cloudlog.info(f"StereoD init: enabled={self.enabled}, baseline={self.stereo_baseline_m * 1000:.0f}mm")

    def _init_sgm(self, width: int, height: int) -> bool:
        """Initialize SGM via HAL GPU backend."""
        if self.gpu is None:
            self._set_fault("gpu_unavailable", "GPU backend not available via HAL")
            return False

        self._sgm_config = SGMConfig(
            target_width=width,
            target_height=height,
            max_disparity=64,
            p1=10,
            p2=120,
            use_8_path=False,
            enable_median_filter=True,
            enable_subpixel=True,
            max_runtime_ms=MAX_SGM_LATENCY_MS
        )

        try:
            self._sgm = SGM(self._sgm_config, target="auto")
            if self._sgm.is_available():
                self._gpu_sgm_available = True
                device_info = self._sgm.get_device_info()
                backend = device_info.get('backend', 'unknown')
                cloudlog.info(f"SGM initialized: {device_info.get('name', 'unknown')} (backend: {backend})")
                return True
            else:
                self._sgm.release()
                self._sgm = None
        except SGMDeviceError as e:
            cloudlog.warning(f"SGM device error: {e}")
        except Exception as e:
            cloudlog.warning(f"SGM initialization failed: {e}")

        self._set_fault("gpu_unavailable", "SGM not available")
        return False

    def _set_fault(self, reason: str, message: str) -> None:
        """Set fault state."""
        self._fault = True
        self._fault_reason = reason
        cloudlog.error(f"FAULT: {reason} - {message}")

    def _compute_disparity(self, left_data: bytes, right_data: bytes) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Compute disparity using SGM via HAL."""
        if not self._gpu_sgm_available or self._sgm is None:
            return None, None

        left_gray = _nv12_to_gray(left_data, self.width, self.height)
        right_gray = _nv12_to_gray(right_data, self.width, self.height)

        left_rect = cv2.remap(left_gray, self.left_map1, self.left_map2, cv2.INTER_LINEAR)
        right_rect = cv2.remap(right_gray, self.right_map1, self.right_map2, cv2.INTER_LINEAR)

        try:
            result = self._sgm.compute(left_rect, right_rect)

            if not result.success:
                raise SGMError(result.error_message or "SGM computation failed")

            self._metrics.record_latency("sgm", result.inference_time_ms)
            self._consecutive_failures = 0

            if result.inference_time_ms > MAX_SGM_LATENCY_MS:
                cloudlog.warning(f"SGM latency high: {result.inference_time_ms:.1f}ms")

            return result.disparity, result.confidence

        except SGMTimeoutError as e:
            self._consecutive_failures += 1
            cloudlog.warning(f"SGM timeout: {e}")
        except (SGMDeviceError, SGMError) as e:
            self._consecutive_failures += 1
            cloudlog.warning(f"SGM error: {e}")
        except Exception as e:
            self._consecutive_failures += 1
            cloudlog.warning(f"SGM unexpected error: {e}")

        if self._consecutive_failures >= self._FAULT_THRESHOLD:
            self._set_fault("sgm_consecutive_failures", f"{self._consecutive_failures} consecutive failures")

        return None, None

    def run(self):
        """Main loop."""
        if not self.enabled:
            cloudlog.info("StereoD disabled — exiting")
            return

        cloudlog.info("Connecting to VisionIPC...")
        while not self.vipc_left.connect(False):
            time.sleep(0.1)
        while not self.vipc_right.connect(False):
            time.sleep(0.1)
        while not self.vipc_wide.connect(False):
            time.sleep(0.1)
        cloudlog.info("StereoD connected")

        try:
            while True:
                buf_l = self.vipc_left.recv()
                buf_r = self.vipc_right.recv()
                self.vipc_wide.recv(timeout_ms=0)

                if buf_l is None or buf_r is None:
                    self.rk.keep_time()
                    continue

                # Initialize calibration and SGM
                if self.width != buf_l.width or self.height != buf_l.height:
                    self.width = buf_l.width
                    self.height = buf_l.height
                    if not self._init_sgm(self.width, self.height):
                        cloudlog.error("Failed to initialize SGM")

                ts = int(time.monotonic() * 1e9)

                # Compute disparity via HAL
                disparity, confidence = self._compute_disparity(
                    bytes(buf_l.data), bytes(buf_r.data)
                )

                # Publish outputs
                self._publish(disparity, confidence, ts)

                self._metrics.record_fps('output')
                self.rk.keep_time()

        finally:
            if self._sgm is not None:
                self._sgm.release()
            self.client.release()

    def _publish(self, disparity, confidence, ts):
        """Publish stereo outputs."""
        status_msg = messaging.new_message('stereoStatus')
        ss = status_msg.stereoStatus
        ss.timestamp = ts
        ss.enabled = self.enabled
        ss.fault = self._fault
        ss.faultReason = self._fault_reason
        ss.consecutiveFailures = self._consecutive_failures

        latency_stats = self._metrics.get_latency_stats('sgm')
        ss.sgmLatencyMs = latency_stats.get('sgm', {}).get('avg_ms', 0.0)

        self.pm.send('stereoStatus', status_msg)


def main() -> int:
    """Entry point."""
    try:
        logging.basicConfig(level=logging.INFO)
        StereoD().run()
        return 0
    except Exception as e:
        cloudlog.exception(f"StereoD fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
