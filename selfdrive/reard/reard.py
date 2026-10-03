#!/usr/bin/env python3
"""
reard.py — Rear Camera Perception Daemon (AHD → UVC)

Pure camera RCTA (Rear Cross Traffic Alert) for ExoPilot 01M/02.
Consumes BGR frames from uvcd VisionIPC (VISION_STREAM_REAR),
runs object detection, and publishes rearDetections.

No radar dependency. No cross-camera handover (single camera).
"""

import logging
import time

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

VISION_STREAM_REAR = VisionStreamType.VISION_STREAM_REAR

from openpilot.selfdrive.sided.bev_reprojector import (SideCameraGeometry, geometry_from_calibration, hal_geometry, make_rear_geometry,
                                                       reproject_side_camera)
from openpilot.selfdrive.sided.yolo_detector import YoloDetector
from openpilot.selfdrive.sided.simple_tracker import SimpleTracker, SideObject
from openpilot.selfdrive.sided.camera_health import CameraHealthTracker

RATE = 20  # 20 Hz

# Object classes relevant for RCTA (vehicles, pedestrians, cyclists)
RELEVANT_CLASSES = frozenset({
  'person', 'bicycle', 'motorcycle', 'car', 'bus', 'truck',
})


class SideObjectFromDetection:
  """Adapter: convert RKNN/CPU detection → SideObject for tracker."""

  def __init__(self, det):
    self.label = det.label
    self.confidence = det.confidence
    self.bbox = det.bbox  # [x1, y1, x2, y2]
    self.distance_m = det.distance_m if hasattr(det, 'distance_m') else 0.0
    self.lateral_m = det.lateral_m if hasattr(det, 'lateral_m') else 0.0
    self.track_id = -1


def _to_tracker_objects(detections) -> list[SideObject]:
  """Convert detector output to SideObject list for SimpleTracker."""
  objs = []
  for det in detections:
    if det.label not in RELEVANT_CLASSES:
      continue
    so = SideObject()
    so.label = det.label
    so.confidence = det.confidence
    so.bbox_2d = det.bbox_2d
    so.distance_m = 0.0  # set from the ground plane in RknnRearProcessor
    so.lateral_m = 0.0
    so.track_id = -1
    objs.append(so)
  return objs


class RearProcessor:
  """CPU fallback processor (same as SideProcessor but for rear)."""

  def __init__(self):
    self.tracker = SimpleTracker(max_age=5)

  def detect(self, frame_bgr: np.ndarray | None) -> list[SideObject]:
    if frame_bgr is None:
      return []
    # CPU fallback: no neural network, use simple motion detection / blob
    # For now, return empty — rear CPU detection is not implemented.
    # The RKNN path (RknnRearProcessor) is the production path.
    return []

  def analyze_quality(self, frame_bgr: np.ndarray):
    """Return basic frame quality metrics."""
    gray = frame_bgr.mean(axis=2)
    return {
      'brightness': float(gray.mean()),
      'contrast': float(gray.std()),
    }


class RknnRearProcessor:
  """YOLOv8 on RKNN for the rear camera, every frame (20 Hz).

  Shares NPU core 1 with the two side cameras. Segmentation of the rear
  camera runs on the PCIe card in segd.
  """

  def __init__(self, detector: YoloDetector | None = None, geometry: SideCameraGeometry | None = None):
    self.detector = detector or YoloDetector("reard")
    self.tracker = SimpleTracker(max_age=5)
    self._geometry = geometry
    self._geometry_hw: tuple[int, int] | None = None

  @property
  def is_available(self) -> bool:
    return self.detector.is_available

  def geometry(self, frame_hw: tuple[int, int]) -> SideCameraGeometry:
    """Rear-camera geometry for this frame size: exopilot hal's tf tree (nominal
    mounting + camera_calibrationd's calibration), else CalibrationStorage,
    else the nominal defaults."""
    if self._geometry is None or (self._geometry_hw is not None and self._geometry_hw != frame_hw):
      h, w = frame_hw
      geo = hal_geometry('rear', w, h)
      if geo is not None:
        self._geometry, self._geometry_hw = geo, frame_hw
        return geo
      calib = None
      try:
        from openpilot.selfdrive.locationd.calibration_storage import CalibrationStorage
        merged = CalibrationStorage.get_merged_calibration()
        calib = merged.get_camera('rear') if merged is not None else None
      except Exception as e:
        cloudlog.debug(f"RearD: no rear calibration ({e})")
      if calib is not None:
        self._geometry = geometry_from_calibration('rear', calib, default_img_w=w, default_img_h=h)
      else:
        self._geometry = make_rear_geometry(img_w=w, img_h=h)
        cloudlog.info("RearD: rear camera not calibrated -- default mounting geometry")
      self._geometry_hw = frame_hw
    return self._geometry

  def detect(self, frame_bgr: np.ndarray | None) -> list[SideObject]:
    if frame_bgr is None or not self.is_available:
      return []
    dets = self.detector.detect(frame_bgr)
    objs = _to_tracker_objects(dets)
    # Ground-plane position of each box's bottom-centre, in metres: x
    # negative behind the rear camera, y positive to the vehicle's left. It
    # used to be 400 / box height for x, and a -1..1 image position
    # published as metres for y.
    geo = self.geometry(frame_bgr.shape[:2])
    for obj in objs:
      x_m, y_m, _, _, _ = reproject_side_camera(obj.bbox_2d, obj.label, frame_bgr.shape[:2], geo)
      obj.distance_m, obj.lateral_m = x_m, y_m
    return self.tracker.update(objs)


class RearD:
  """Rear Camera Perception Daemon."""

  def __init__(self) -> None:
    set_daemon_affinity("reard")

    self.pm = messaging.PubMaster(['rearDetections', 'rearStatus'])
    self.sm = messaging.SubMaster(['rearCameraState'])

    self.rk = Ratekeeper(RATE, print_delay_threshold=None)
    self.params = Params()
    self.enabled = self.params.get_bool("EOPRearCameraEnabled")

    self.frame_id = 0
    self._camera_health = CameraHealthTracker()
    self.cpu_processor = RearProcessor()
    self.rknn_processor = RknnRearProcessor()
    self.use_rknn = self.rknn_processor.is_available

    self._vipc_rear = None
    self._init_visionipc()

    cloudlog.info(
      "RearD initialized: enabled=%s, rknn=%s, visionipc=%s",
      self.enabled, self.use_rknn, HAS_VISIONIPC,
    )

  def _init_visionipc(self) -> None:
    if not HAS_VISIONIPC:
      cloudlog.warning("RearD: VisionIPC not available")
      return

    try:
      self._vipc_rear = VisionIpcClient("uvcd", VISION_STREAM_REAR, False)
      if not self._vipc_rear.connect(False):
        cloudlog.warning("RearD: rear VisionIPC not available")
        self._vipc_rear = None
      else:
        cloudlog.info("RearD: rear VisionIPC connected (%dx%d)",
                      self._vipc_rear.width, self._vipc_rear.height)
    except Exception as e:
      cloudlog.warning("RearD: rear VisionIPC init failed: %s", e)
      self._vipc_rear = None

  def _get_frame(self) -> np.ndarray | None:
    if self._vipc_rear is None:
      return None
    try:
      buf = self._vipc_rear.recv(timeout_ms=50)
      if buf is None:
        return None
      h = self._vipc_rear.height or 480
      w = self._vipc_rear.width or 640
      frame = np.frombuffer(buf.data, dtype=np.uint8).reshape((h, w, 3))
      return frame
    except Exception as e:
      cloudlog.debug("RearD: VisionIPC frame retrieval failed: %s", e)
      return None

  def _publish(self, tracks: list[SideObject], quality: dict | None,
               ts: int, proc_time_ms: float) -> None:
    # rearDetections
    msg = messaging.new_message('rearDetections', valid=True)
    msg.rearDetections.frameId = self.frame_id
    msg.rearDetections.timestamp = ts / 1e9
    msg.rearDetections.numTracks = len(tracks)
    msg.rearDetections.cameraSource = "rear"

    if tracks:
      items = msg.rearDetections.init('detections', len(tracks))
      for i, track in enumerate(tracks):
        items[i].trackId = track.uid
        items[i].className = track.label
        items[i].confidence = track.confidence
        items[i].x = track.distance_m
        items[i].y = track.lateral_m
        items[i].vx = track.velocity_mps
        items[i].vy = 0.0
        items[i].sigmaX = 4.0
        items[i].sigmaY = 2.0
        items[i].cameraSource = 'rear'
    self.pm.send('rearDetections', msg)

    # rearStatus
    camera_fault, camera_reason = self._camera_health.check(['rear'] if self.enabled else [])

    status_msg = messaging.new_message('rearStatus', valid=True)
    ss = status_msg.rearStatus
    ss.enabled = self.enabled
    ss.fault = camera_fault
    ss.faultReason = camera_reason
    ss.consecutiveFailures = 0
    ss.numTracks = len(tracks)
    ss.processingTimeMs = round(proc_time_ms, 2)
    self.pm.send('rearStatus', status_msg)

  def run(self) -> None:
    if not self.enabled:
      cloudlog.info("RearD disabled — exiting")
      return

    cloudlog.info("RearD running (rear camera perception)")

    try:
      while True:
        self.sm.update(0)

        frame = None
        if self.sm.updated['rearCameraState']:
          frame = self._get_frame()
          if frame is not None:
            self._camera_health.mark_frame('rear')

        quality = None
        if frame is not None:
          quality = self.cpu_processor.analyze_quality(frame)

        # Inference
        t0 = time.monotonic()
        if self.use_rknn:
          dets = self.rknn_processor.detect(frame)
        else:
          dets = self.cpu_processor.detect(frame)
        proc_time_ms = (time.monotonic() - t0) * 1000.0

        ts = int(time.monotonic() * 1e9)
        self._publish(dets, quality, ts, proc_time_ms)

        self.frame_id = (self.frame_id + 1) & 0xFFFFFF
        self.rk.keep_time()
    except Exception:  # noqa: TRY203
      raise


def main() -> int:
  if not HARDWARE.has_rear_camera():
    cloudlog.error("reard: platform does not support rear camera — exiting")
    return 1
  try:
    logging.basicConfig(level=logging.INFO)
    RearD().run()
    return 0
  except KeyboardInterrupt:
    cloudlog.info("RearD stopped")
    return 0
  except Exception as e:
    cloudlog.exception(f"RearD fatal error: {e}")
    raise


if __name__ == "__main__":
  exit(main())
