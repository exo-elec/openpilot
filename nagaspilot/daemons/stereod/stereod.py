#!/usr/bin/env python3
"""
StereoD — optional GPU stereo depth source for fusion

Uses centralized InferenceD HAL for all compute:
- GPU-only OpenCL for SGM stereo depth

Publishes stereoDepth only. 2D YOLO on the stereo pair and PP-LiteSeg
segmentation are not implemented here: object detection runs on RKNN via
monod/sided/reard, and segmentation runs on the camera-tier card via segd.

No direct hardware access - all through HAL.
"""

from __future__ import annotations

import logging
import time

import numpy as np

import cereal.messaging as messaging
from msgq.visionipc import VisionIpcClient
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.system.hardware.registry import PlatformRegistry
from openpilot.common.perf_monitor import PerformanceMonitor
from openpilot.common.swaglog import cloudlog

# Centralized HAL - ONLY way to access hardware
from openpilot.system.inferenced import InferenceClient

# SGM via HAL
from openpilot.nagaspilot.daemons.stereod.stereo_input import luma_plane, capture_timestamp, calibration_maps, calibration_id
from openpilot.system.inferenced.opencl_rectify import OpenCLRectifier
from openpilot.nagaspilot.daemons.stereod.sgm import SGM, SGMConfig, SGMError, SGMDeviceError, SGMTimeoutError

# Constants
RATE = 20  # Hz
STEREO_LEFT_STREAM = 4
STEREO_RIGHT_STREAM = 5
FAULT_THRESHOLD = 3
MAX_SGM_LATENCY_MS = 50.0
BACKEND_RETRY_S = 2.0
MAX_OUTPUT_AGE_NS = 200_000_000


class StereoD:
    """
    2D stereo depth daemon using centralized HAL.

    All hardware access through InferenceClient:
    - GPU for SGM
    """

    def __init__(self):
        set_daemon_affinity("stereod")

        # Create HAL client - ONLY way to access hardware
        self.client = InferenceClient("stereod")

        self.params = Params()
        self.enabled = self.params.get_bool("EOPStereoEnabled")

        # Acquired lazily on the first valid camera pair, with bounded retries.
        self.gpu = None
        self._next_init_attempt = 0.0

        # Camera geometry
        hardware = PlatformRegistry.create()
        self._hardware = hardware
        self.stereo_baseline_m = None  # Established only from validated factory calibration.

        # SGM state
        self._sgm: SGM | None = None
        self._rectifier = None
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
        self._calibration_id = ""
        self.frame_id = 0
        self._last_capture = 0
        self._left_support = self._right_support = None

        # VisionIPC
        self.vipc_left = VisionIpcClient("v4l2d", STEREO_LEFT_STREAM, True)
        self.vipc_right = VisionIpcClient("v4l2d", STEREO_RIGHT_STREAM, True)

        # Publishers. 2D YOLO on the stereo pair (stereoDetections) is not
        # implemented -- detection runs on RKNN via monod/sided/reard instead.
        self.pm = messaging.PubMaster([
            'stereoDepth',
            'stereoStatus',
        ])
        self.rk = Ratekeeper(RATE, print_delay_threshold=None)

        cloudlog.info(f"StereoD init: enabled={self.enabled}, awaiting factory stereo calibration")

    def _init_sgm(self, width: int, height: int) -> bool:
        """Initialize SGM via HAL GPU backend."""
        self._next_init_attempt = time.monotonic() + BACKEND_RETRY_S
        self._gpu_sgm_available = False
        self.Q = None
        self._calibration_id = ""
        self.stereo_baseline_m = None
        self.left_map1 = self.left_map2 = None
        self.right_map1 = self.right_map2 = None
        self._left_support = self._right_support = None
        if self._sgm is not None or self._rectifier is not None:
            self._drop_backend()
        if self.gpu is None:
            try:
                self.gpu = self.client.gpu()
            except RuntimeError as exc:
                self._set_fault("gpu_unavailable", str(exc))
                return False
        try:
            cal = self._hardware.load_stereo_intrinsics()
            (self.left_map1, self.left_map2, self.right_map1, self.right_map2,
             self.Q, self._left_support, self._right_support) = calibration_maps(cal, width, height)
            self.stereo_baseline_m = float(np.linalg.norm(cal.T))
            self._calibration_id = calibration_id(cal)
        except Exception as exc:
            self.Q = None
            self._calibration_id = ""
            self.stereo_baseline_m = None
            self._set_fault("calibration_invalid", str(exc))
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
            self._rectifier = OpenCLRectifier(self.gpu, (self.left_map1, self.left_map2, self.right_map1, self.right_map2))
            self._sgm = SGM(self._sgm_config, target="gpu", gpu=self.gpu)
            if self._sgm.is_available():
                self._gpu_sgm_available = True
                self._rows, self._cols = np.indices((height, width), dtype=np.int32)
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
        changed = not self._fault or self._fault_reason != reason
        self._fault = True
        self._fault_reason = reason
        if changed:
            cloudlog.error(f"FAULT: {reason} - {message}")

    def _drop_backend(self) -> None:
        """Release failed GPU work and permit a bounded re-acquisition attempt."""
        self._gpu_sgm_available = False
        self._next_init_attempt = time.monotonic() + BACKEND_RETRY_S
        try:
            if self._sgm is not None:
                self._sgm.release()
        except Exception as exc:
            cloudlog.warning(f"SGM release failed: {exc}")
        finally:
            self._sgm = None
            rectifier, self._rectifier = self._rectifier, None
            if rectifier is not None:
                try:
                    rectifier.close()
                except Exception as exc:
                    cloudlog.warning(f"Rectifier release failed: {exc}")
            try:
                self.client.release()
            except Exception as exc:
                cloudlog.warning(f"GPU release failed: {exc}")
            self.gpu = None

    def _compute_disparity(self, left_data: bytes, right_data: bytes) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Compute disparity using SGM via HAL."""
        if not self._gpu_sgm_available or self._sgm is None:
            return None, None

        try:
            left_gray = luma_plane(left_data, self.width, self.height, self.vipc_left.stride)
            right_gray = luma_plane(right_data, self.width, self.height, self.vipc_right.stride)
            left_rect, right_rect = self._rectifier.remap(left_gray, right_gray)
            result = self._sgm.compute(left_rect, right_rect)

            if not result.success:
                raise SGMError(result.error_message or "SGM computation failed")

            if not np.isfinite(result.inference_time_ms) or not 0 <= result.inference_time_ms <= MAX_SGM_LATENCY_MS:
                raise SGMTimeoutError("Stereo backend returned an invalid or late completion time")
            expected = (self.height, self.width)
            if (result.disparity.shape != expected or result.confidence.shape != expected or
                    not np.isfinite(result.disparity).all() or not np.isfinite(result.confidence).all() or
                    np.any(result.disparity < 0) or np.any(result.disparity > self._sgm_config.max_disparity) or
                    np.any(result.confidence < 0) or np.any(result.confidence > 1)):
                raise SGMDeviceError("Invalid GPU stereo output shape/range")
            self._metrics.record_latency("sgm", result.inference_time_ms)
            # Reject rectification footprint holes in both matching views.
            matched = np.rint(self._cols - result.disparity).astype(np.int32)
            inside = (matched >= 0) & (matched < self.width)
            supported = self._left_support & inside & self._right_support[self._rows, np.clip(matched, 0, self.width-1)]
            supported &= (result.disparity > 0) & (result.confidence > 0)
            result.disparity[~supported] = 0
            result.confidence[~supported] = 0

            self._consecutive_failures = 0
            self._fault = False
            self._fault_reason = ""

            return result.disparity, result.confidence

        except SGMTimeoutError as e:
            self._consecutive_failures += 1
            cloudlog.warning(f"SGM timeout: {e}")
        except SGMDeviceError as e:
            self._consecutive_failures += 1
            self._set_fault("gpu_unavailable", str(e))
            self._drop_backend()
        except SGMError as e:
            self._consecutive_failures += 1
            cloudlog.warning(f"SGM error: {e}")
        except Exception as e:
            self._consecutive_failures += 1
            cloudlog.warning(f"SGM unexpected error: {e}")

        if self._consecutive_failures >= self._FAULT_THRESHOLD:
            self._set_fault("sgm_consecutive_failures", f"{self._consecutive_failures} consecutive failures")
            if self._sgm is not None:
                self._drop_backend()

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
        cloudlog.info("StereoD connected")

        try:
            while True:
                buf_l = self.vipc_left.recv()
                buf_r = self.vipc_right.recv()

                if buf_l is None or buf_r is None:
                    self._set_fault("stereo_input_missing", "Stereo camera pair unavailable")
                    self._publish(None, None, time.monotonic_ns())
                    self.rk.keep_time()
                    continue

                try:
                    if (buf_l.width, buf_l.height) != (buf_r.width, buf_r.height):
                        raise ValueError("Stereo image dimensions differ")
                    ts = capture_timestamp(self.vipc_left.timestamp_sof, self.vipc_right.timestamp_sof,
                                           time.monotonic_ns(), self._last_capture)
                except ValueError as exc:
                    self._set_fault("stereo_input_invalid", str(exc))
                    self._publish(None, None, time.monotonic_ns())
                    self.rk.keep_time()
                    continue
                self._last_capture = ts
                self.frame_id = self.vipc_left.frame_id

                resized = self.width != buf_l.width or self.height != buf_l.height
                if resized or (not self._gpu_sgm_available and time.monotonic() >= self._next_init_attempt):
                    self.width = buf_l.width
                    self.height = buf_l.height
                    if not self._init_sgm(self.width, self.height):
                        self._publish(None, None, ts)
                        self.rk.keep_time()
                        continue

                if not self._gpu_sgm_available:
                    self._publish(None, None, ts)
                    self.rk.keep_time()
                    continue

                # Compute disparity via HAL
                disparity, confidence = self._compute_disparity(
                    bytes(buf_l.data), bytes(buf_r.data)
                )

                if time.monotonic_ns() - ts > MAX_OUTPUT_AGE_NS:
                    self._set_fault("stereo_output_stale", "Capture-to-output age exceeds 200 ms")
                    disparity = confidence = None

                # Publish outputs
                self._publish(disparity, confidence, ts)

                self._metrics.record_fps('output')
                self.rk.keep_time()

        finally:
            self._drop_backend()

    def _publish(self, disparity, confidence, ts):
        """Publish stereo outputs."""
        if disparity is not None:
            depth_msg = messaging.new_message('stereoDepth')
            sd = depth_msg.stereoDepth
            sd.timestamp = ts
            sd.frameId = self.frame_id
            sd.width = self.width
            sd.height = self.height
            sd.calibrationId = self._calibration_id
            sd.disparityMap = disparity.astype(np.float32).tobytes()
            if confidence is not None:
                sd.confidenceMap = confidence.astype(np.float32).tobytes()
                valid = confidence > 0
                sd.meanDisparity = float(disparity[valid].mean()) if np.any(valid) else 0.0
                sd.validPixels = int(np.count_nonzero(valid))
            depth_msg.valid = bool(self.enabled and not self._fault and self._calibration_id and
                                   confidence is not None and sd.validPixels > 0)
            self.pm.send('stereoDepth', depth_msg)

        status_msg = messaging.new_message('stereoStatus')
        status_msg.valid = True  # A valid health report can describe a degraded source.
        ss = status_msg.stereoStatus
        ss.timestamp = ts
        ss.enabled = self.enabled
        ss.fault = self._fault
        ss.faultReason = self._fault_reason
        ss.consecutiveFailures = self._consecutive_failures

        latency_stats = self._metrics.get_latency_stats('sgm')
        ss.processingTimeMs = latency_stats.get('sgm', {}).get('avg_ms', 0.0)
        ss.frameId = self.frame_id
        ss.hasCalibration = self.Q is not None

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
