#!/usr/bin/env python3
"""
sided.py — Side Camera Perception Daemon (AHD → UVC)

ExoPilot 01M (RK3588).

Hardware:
  - side_left / side_right are UVC cameras.
  - ExoPilot 01M uses a USB 3.0 hub (RTS5411S) to provide the USB 2.0 ports.

Pipeline:
  - Consumes BGR frames from uvcd VisionIPC server (streams 8 / 9)
  - Per-camera: inference → BEV reprojection → SimpleTracker
  - HandoverManager for cross-camera global UIDs
  - Publishes sideDetections + sideStatus

Future inference models (Hailo HEF):
  - yolo_side:      side-camera object detection (vehicles, pedestrians, cyclists)
  - sceneseg_side:  drivable-area / curb segmentation
"""

import logging
import math
import time
from dataclasses import dataclass

import numpy as np

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE

# NOTE: capability check happens in main() — a module-level exit() would kill
# any process that merely imports this module (tests, tooling).

# VisionIPC integration
try:
  from msgq.visionipc import VisionIpcClient, VisionStreamType
  HAS_VISIONIPC = True
except ImportError:
  HAS_VISIONIPC = False

VISION_STREAM_SIDE_LEFT = VisionStreamType.VISION_STREAM_SIDE_LEFT
VISION_STREAM_SIDE_RIGHT = VisionStreamType.VISION_STREAM_SIDE_RIGHT

from openpilot.selfdrive.sided.bev_reprojector import (
  SideCameraGeometry, reproject_side_camera, make_default_geometry,
  geometry_from_calibration,
)
from openpilot.selfdrive.locationd.calibration_storage import CalibrationStorage
from openpilot.selfdrive.sided.simple_tracker import SimpleTracker, SideObject
from openpilot.selfdrive.sided.handover_manager import HandoverManager
from openpilot.selfdrive.sided.hailo_side_detector import HailoSideDetector

RATE = 20  # 20 Hz

# Object classes we care about for BSD/RCTA
RELEVANT_CLASSES = frozenset({
  'person', 'bicycle', 'motorcycle', 'car', 'bus', 'truck',  # COCO classes only
})


@dataclass
class FrameQuality:
  mean_brightness: float
  underexposed: bool
  overexposed: bool


# ---------------------------------------------------------------------------
class HailoSideProcessor:
  """Hailo-8 processor for side camera detection / segmentation.

  Uses HailoSideDetector to run YOLOv8-nano on side camera frames.
  When active, returns List[SideObject] for the tracker.
  """

  def __init__(self):
    self._detector = HailoSideDetector(daemon_name="sided")

  @property
  def is_available(self) -> bool:
    return self._detector.is_available

  def detect(self, frame: np.ndarray | None) -> list[SideObject]:
    """Run Hailo inference on a single side camera frame."""
    return self._detector.detect(frame)


# ---------------------------------------------------------------------------
# CPU fallback processor
# ---------------------------------------------------------------------------
class SideProcessor:
  """CPU-side quality analysis and placeholder detection."""

  def analyze_quality(self, frame: np.ndarray) -> FrameQuality:
    gray = frame.mean() if frame.ndim == 2 else frame[:, :, 0].mean()
    mean_bright = float(gray)
    under = mean_bright < 30.0
    over = mean_bright > 250.0
    return FrameQuality(mean_bright, under, over)

  def detect(self, frame: np.ndarray | None) -> list[SideObject]:
    # CPU fallback: no neural inference yet
    return []


# ---------------------------------------------------------------------------
# Per-camera tracker: inference → BEV → SimpleTracker
# ---------------------------------------------------------------------------
class SingleCameraTracker:
  """Per-camera tracker: inference → BEV reprojection → SimpleTracker."""

  def __init__(
    self,
    camera_name: str,
    geometry: SideCameraGeometry,
    obj_threshold: float = 0.30,
  ) -> None:
    self.camera_name = camera_name
    self.geometry = geometry
    self.obj_threshold = obj_threshold
    self.tracker = SimpleTracker()

  def process_frame(
    self,
    frame_bgr: np.ndarray,
    raw_detections: list[SideObject],
  ) -> list[SideObject]:
    """Process one frame and return tracked SideObjects.

    Args:
      frame_bgr: BGR image from the camera
      raw_detections: raw detections from inference (empty if no inference)

    Returns:
      List of SideObject with per-camera UIDs (not yet global).
    """
    if not raw_detections:
      self.tracker.update([])
      return []

    perceived: list[SideObject] = []
    for det in raw_detections:
      if det.label not in RELEVANT_CLASSES:
        continue
      if det.confidence < self.obj_threshold:
        continue

      # BEV reprojection
      x_m, y_m, z_m, w_m, l_m = reproject_side_camera(
        det.bbox_2d,
        det.label,
        frame_bgr.shape[:2],
        self.geometry,
      )

      obj = SideObject(
        uid=-1,
        label=det.label,
        confidence=det.confidence,
        distance_m=x_m,
        lateral_m=y_m,
        height_m=z_m,
        velocity_mps=0.0,
        bbox_2d=det.bbox_2d,
        width_m=w_m,
        length_m=l_m,
      )
      perceived.append(obj)

    return self.tracker.update(perceived)


# ---------------------------------------------------------------------------
# Core orchestrator for both side cameras + handover manager
# ---------------------------------------------------------------------------
class SideTrackerCore:
  """Core orchestrator for both side cameras + handover manager."""

  def __init__(
    self,
    obj_threshold: float = 0.30,
    left_geometry: SideCameraGeometry | None = None,
    right_geometry: SideCameraGeometry | None = None,
  ) -> None:
    self.obj_threshold = obj_threshold

    # Try to load calibrated geometry; fall back to defaults
    if left_geometry is None or right_geometry is None:
      left_geometry, right_geometry = self._load_calibrated_geometry(
        left_geometry, right_geometry
      )

    self.left_tracker = SingleCameraTracker(
      camera_name='side_left',
      geometry=left_geometry,
      obj_threshold=obj_threshold,
    )
    self.right_tracker = SingleCameraTracker(
      camera_name='side_right',
      geometry=right_geometry,
      obj_threshold=obj_threshold,
    )
    self.handover = HandoverManager()
    self._frame_count = 0

  @staticmethod
  def _load_calibrated_geometry(
    left_geo: SideCameraGeometry | None,
    right_geo: SideCameraGeometry | None,
  ) -> tuple[SideCameraGeometry, SideCameraGeometry]:
    """Load side camera geometry from CalibrationStorage.

    Falls back to make_default_geometry() if no calibration exists
    or if side camera entries are missing.
    """
    try:
      calib = CalibrationStorage.get_merged_calibration()
      if calib is None:
        cloudlog.info("SideTrackerCore: no calibration found — using defaults")
        return (
          left_geo or make_default_geometry('side_left'),
          right_geo or make_default_geometry('side_right'),
        )
    except Exception as e:
      cloudlog.warning("SideTrackerCore: failed to load calibration: %s — using defaults", e)
      return (
        left_geo or make_default_geometry('side_left'),
        right_geo or make_default_geometry('side_right'),
      )

    # side_left
    if left_geo is None:
      side_left_calib = calib.get_camera('side_left')
      if side_left_calib is not None:
        left_geo = geometry_from_calibration('side_left', side_left_calib)
        cloudlog.info(
          "SideTrackerCore: loaded calibrated side_left geometry " +
          "(yaw=%.2f°, pitch=%.2f°, height=%.2f m)",
          math.degrees(left_geo.yaw_rad),
          math.degrees(left_geo.pitch_rad),
          left_geo.cam_z_m,
        )
      else:
        cloudlog.info("SideTrackerCore: no side_left calibration — using default")
        left_geo = make_default_geometry('side_left')

    # side_right
    if right_geo is None:
      side_right_calib = calib.get_camera('side_right')
      if side_right_calib is not None:
        right_geo = geometry_from_calibration('side_right', side_right_calib)
        cloudlog.info(
          "SideTrackerCore: loaded calibrated side_right geometry " +
          "(yaw=%.2f°, pitch=%.2f°, height=%.2f m)",
          math.degrees(right_geo.yaw_rad),
          math.degrees(right_geo.pitch_rad),
          right_geo.cam_z_m,
        )
      else:
        cloudlog.info("SideTrackerCore: no side_right calibration — using default")
        right_geo = make_default_geometry('side_right')

    return left_geo, right_geo

  def process_frames(
    self,
    left_frame: np.ndarray | None,
    right_frame: np.ndarray | None,
    left_dets: list[SideObject],
    right_dets: list[SideObject],
  ) -> list[SideObject]:
    """Process both camera frames and return globally-tracked objects."""
    camera_tracks: dict[str, list[SideObject]] = {}

    if left_frame is not None:
      camera_tracks['side_left'] = self.left_tracker.process_frame(left_frame, left_dets)

    if right_frame is not None:
      camera_tracks['side_right'] = self.right_tracker.process_frame(right_frame, right_dets)

    self._frame_count += 1
    return self.handover.update(camera_tracks)

  def reset(self) -> None:
    """Reset all trackers (e.g., after disengagement)."""
    self.handover.reset()
    self.left_tracker.tracker.reset()
    self.right_tracker.tracker.reset()
    self._frame_count = 0


# ---------------------------------------------------------------------------
# SideD daemon
# ---------------------------------------------------------------------------
class SideD:

  def __init__(self) -> None:
    set_daemon_affinity("sided")

    self.pm = messaging.PubMaster(['sideDetections', 'sideStatus'])
    self.sm = messaging.SubMaster([
      'leftCameraState',
      'rightCameraState',
    ])

    self.rk = Ratekeeper(RATE, print_delay_threshold=None)
    self.params = Params()
    self.enabled = self.params.get_bool("EOPSideCamerasEnabled")

    # EOP: Per-camera enablement + swap support
    self._left_enabled = self.params.get_bool("EOPLeftCameraEnabled")
    self._right_enabled = self.params.get_bool("EOPRightCameraEnabled")
    self._cameras_swapped = self.params.get_bool("EOPSideCamerasSwapped")

    self.frame_id = 0
    self.cpu_processor = SideProcessor()
    self.hailo_processor = HailoSideProcessor()
    self.use_hailo = self.hailo_processor.is_available

    self._vipc_left = None
    self._vipc_right = None
    self._init_visionipc()

    # Load geometry; swap if user requested via EOPSideCamerasSwapped
    left_geo, right_geo = self._load_geometry_pair()
    if self._cameras_swapped:
      left_geo, right_geo = right_geo, left_geo
      cloudlog.info("SideD: cameras swapped via EOPSideCamerasSwapped")

    self.tracker_core = SideTrackerCore(left_geometry=left_geo, right_geometry=right_geo)

    cloudlog.info(
      "SideD initialized: enabled=%s, left=%s, right=%s, swapped=%s, hailo=%s, visionipc=%s",
      self.enabled, self._left_enabled, self._right_enabled,
      self._cameras_swapped, self.use_hailo, HAS_VISIONIPC,
    )

  def _load_geometry_pair(self):
    """Load calibrated geometry for both side cameras."""
    try:
      calib = CalibrationStorage.get_merged_calibration()
      if calib is not None:
        left_calib = calib.get_camera('side_left')
        right_calib = calib.get_camera('side_right')
        left_geo = geometry_from_calibration('side_left', left_calib) if left_calib else make_default_geometry('side_left')
        right_geo = geometry_from_calibration('side_right', right_calib) if right_calib else make_default_geometry('side_right')
        return left_geo, right_geo
    except Exception as e:
      cloudlog.warning("SideD: failed to load calibration: %s", e)
    return make_default_geometry('side_left'), make_default_geometry('side_right')

  def _init_visionipc(self) -> None:
    if not HAS_VISIONIPC:
      cloudlog.warning("SideD: VisionIPC not available")
      return

    # When swapped, connect physical left port to RIGHT stream and vice versa
    left_stream = VISION_STREAM_SIDE_RIGHT if self._cameras_swapped else VISION_STREAM_SIDE_LEFT
    right_stream = VISION_STREAM_SIDE_LEFT if self._cameras_swapped else VISION_STREAM_SIDE_RIGHT

    if self._left_enabled:
      try:
        self._vipc_left = VisionIpcClient("uvcd", left_stream, False)
        if not self._vipc_left.connect(False):
          cloudlog.warning("SideD: left VisionIPC not available")
          self._vipc_left = None
        else:
          cloudlog.info("SideD: left VisionIPC connected (stream=%s)", left_stream)
      except Exception as e:
        cloudlog.warning("SideD: left VisionIPC init failed: %s", e)
        self._vipc_left = None

    if self._right_enabled:
      try:
        self._vipc_right = VisionIpcClient("uvcd", right_stream, False)
        if not self._vipc_right.connect(False):
          cloudlog.warning("SideD: right VisionIPC not available")
          self._vipc_right = None
        else:
          cloudlog.info("SideD: right VisionIPC connected (stream=%s)", right_stream)
      except Exception as e:
        cloudlog.warning("SideD: right VisionIPC init failed: %s", e)
        self._vipc_right = None

  def _get_frame(self, vipc_client) -> np.ndarray | None:
    if vipc_client is None:
      return None
    try:
      buf = vipc_client.recv(timeout_ms=50)
      if buf is None:
        return None
      h = vipc_client.height or 720
      w = vipc_client.width or 1280
      frame = np.frombuffer(buf.data, dtype=np.uint8).reshape((h, w, 3))
      return frame
    except Exception as e:
      cloudlog.debug("SideD: VisionIPC frame retrieval failed: %s", e)
      return None

  def _publish(self, tracks: list[SideObject], left_quality: FrameQuality | None,
               right_quality: FrameQuality | None, ts: int,
               proc_time_ms: float, left_present: bool, right_present: bool) -> None:
    # sideDetections
    msg = messaging.new_message('sideDetections', valid=True)
    msg.sideDetections.frameId = self.frame_id
    msg.sideDetections.timestamp = ts / 1e9
    msg.sideDetections.numTracks = len(tracks)

    # cameraSource reflects which cameras actually contributed this frame
    if left_present and right_present:
      msg.sideDetections.cameraSource = "both"
    elif left_present:
      msg.sideDetections.cameraSource = "left"
    elif right_present:
      msg.sideDetections.cameraSource = "right"
    else:
      msg.sideDetections.cameraSource = "none"

    if tracks:
      items = msg.sideDetections.init('detections', len(tracks))
      for i, track in enumerate(tracks):
        items[i].className = track.label
        items[i].confidence = track.confidence
        items[i].x = track.distance_m
        items[i].y = track.lateral_m
        # After swap, lateral_m > 0 still means left side of vehicle
        items[i].cameraSource = 'side_left' if track.lateral_m > 0 else 'side_right'
    self.pm.send('sideDetections', msg)

    # sideStatus
    status_msg = messaging.new_message('sideStatus', valid=True)
    ss = status_msg.sideStatus
    ss.enabled = self.enabled
    ss.fault = False
    ss.faultReason = ""
    ss.consecutiveFailures = 0
    ss.numTracks = len(tracks)
    ss.processingTimeMs = round(proc_time_ms, 2)
    self.pm.send('sideStatus', status_msg)

  def run(self) -> None:
    if not self.enabled:
      cloudlog.info("SideD disabled — exiting")
      return

    cloudlog.info("SideD running (side camera perception)")

    try:
      while True:
        self.sm.update(0)

        left_frame = None
        right_frame = None
        left_quality = None
        right_quality = None

        if self.sm.updated['leftCameraState']:
          left_frame = self._get_frame(self._vipc_left)

        if self.sm.updated['rightCameraState']:
          right_frame = self._get_frame(self._vipc_right)

        if left_frame is not None:
          left_quality = self.cpu_processor.analyze_quality(left_frame)

        if right_frame is not None:
          right_quality = self.cpu_processor.analyze_quality(right_frame)

        # Inference: Hailo primary, CPU fallback
        t0 = time.monotonic()
        if self.use_hailo:
          left_dets = self.hailo_processor.detect(left_frame) if self._left_enabled else []
          right_dets = self.hailo_processor.detect(right_frame) if self._right_enabled else []
        else:
          left_dets = self.cpu_processor.detect(left_frame) if self._left_enabled else []
          right_dets = self.cpu_processor.detect(right_frame) if self._right_enabled else []

        # Tracking + BEV + handover
        tracks = self.tracker_core.process_frames(
          left_frame, right_frame, left_dets, right_dets,
        )
        proc_time_ms = (time.monotonic() - t0) * 1000.0

        ts = int(time.monotonic() * 1e9)
        left_present = self._left_enabled and left_frame is not None
        right_present = self._right_enabled and right_frame is not None
        self._publish(tracks, left_quality, right_quality, ts, proc_time_ms,
                      left_present, right_present)

        # frame_id wraparound at 24-bit to avoid overflow
        self.frame_id = (self.frame_id + 1) & 0xFFFFFF
        self.rk.keep_time()
    except Exception as e:
      cloudlog.error("SideD: main loop error: %s", e)
      raise


def main() -> int:
  if not HARDWARE.has_side_cameras():
    cloudlog.error("sided: platform does not support side cameras — exiting")
    return 1
  try:
    logging.basicConfig(level=logging.INFO)
    SideD().run()
    return 0
  except Exception as e:
    cloudlog.exception(f"SideD fatal error: {e}")
    return 1


if __name__ == "__main__":
  exit(main())
